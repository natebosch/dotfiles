"""Unit and tmux integration tests for code_harness.tmux_yank_id."""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from code_harness import cli, snapshot, tmux_yank_id
from code_harness.config import load_config
from code_harness.snapshot import TmuxRunner


def _make_pane_line(pidx: int, pane_id: str, pane_pid: int, active: int) -> str:
  return "\x1f".join(str(x) for x in (pidx, pane_id, pane_pid, active))


class FakeTmuxRunner:

  def __init__(
      self,
      panes_stdout: str,
      current_stdout: str = "s1\x1f0\x1f%1\n",
      buffers: list[str] | None = None,
  ) -> None:
    self.base = ["tmux"]
    self.panes_stdout = panes_stdout
    self.current_stdout = current_stdout
    self.buffers: list[str] = list(buffers or [])
    self.commands: list[tuple[str, ...]] = []

  def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    del check
    self.commands.append(args)
    if args and args[0] == "display-message":
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout=self.current_stdout, stderr=""
      )
    if args and args[0] == "list-panes":
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout=self.panes_stdout, stderr=""
      )
    if args and args[0] == "show-buffer":
      if not self.buffers:
        return subprocess.CompletedProcess(
            [*self.base, *args], 1, stdout="", stderr="no buffers\n"
        )
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout=self.buffers[0], stderr=""
      )
    if args and args[0] == "set-buffer":
      self.buffers.insert(0, args[-1])
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout="", stderr=""
      )
    return subprocess.CompletedProcess(
        [*self.base, *args], 0, stdout="", stderr=""
    )


class TmuxYankIdTests(unittest.TestCase):

  def _load_default_config(self, tmp: str):
    env = {"XDG_CONFIG_HOME": tmp}
    with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
      return load_config(env=env)

  def test_tmux_yank_id_is_hidden_from_top_level_help(self) -> None:
    self.assertIn("tmux-yank-id", cli.HIDDEN_COMMANDS)
    self.assertNotIn("tmux-yank-id", cli.PUBLIC_COMMANDS)
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertNotIn("tmux-yank-id", stdout.getvalue())

  def test_yanks_active_pane_conversation_id(self) -> None:
    conv_id = "11111111-2222-3333-4444-555555555555"
    fake_tmux = FakeTmuxRunner(_make_pane_line(0, "%1", 101, 1))
    procs = {101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""}}

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      toast_log = os.path.join(tmp, "toast.log")
      with (
          mock.patch.dict(
              os.environ,
              {
                  "CODE_HARNESS_TMUX_TOAST_LOG": toast_log,
                  "CODE_HARNESS_TMUX_SYNC_TOAST": "1",
              },
          ),
          mock.patch.object(
              tmux_yank_id, "TmuxRunner", return_value=fake_tmux
          ),
          mock.patch.object(
              tmux_yank_id, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_yank_id,
              "resolve_cli_conversation_and_dir",
              return_value=(conv_id, set(), tmp),
          ),
      ):
        rc = tmux_yank_id.cmd_tmux_yank_id(cfg, None, ["/dev/pts/1"])

      self.assertEqual(rc, 0)
      self.assertEqual(fake_tmux.buffers, [conv_id])
      self.assertEqual(
          pathlib.Path(toast_log).read_text(encoding="utf-8").strip(),
          f"Yanked {conv_id}",
      )

  def test_falls_back_to_other_pane_in_active_window_and_prefers_active(
      self,
  ) -> None:
    panes_out = "\n".join([
        _make_pane_line(0, "%1", 101, 0),
        _make_pane_line(1, "%2", 102, 1),
    ])
    procs_fallback = {
        101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""},
        102: {"pid": 102, "ppid": 1, "argv": ["zsh"], "exe": ""},
    }
    procs_both = {
        101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""},
        102: {"pid": 102, "ppid": 1, "argv": ["agy"], "exe": ""},
    }

    def fake_resolve(cli_info, pane_id, data_dir=None, config=None):
      del cli_info, config
      return f"conv-{pane_id}", set(), data_dir or "/tmp"

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)

      # Active pane is zsh, sibling pane is agy -> picks sibling pane's ID.
      fake_tmux = FakeTmuxRunner(panes_out, current_stdout="s1\x1f0\x1f%2\n")
      with (
          mock.patch.object(
              tmux_yank_id, "TmuxRunner", return_value=fake_tmux
          ),
          mock.patch.object(
              tmux_yank_id,
              "load_process_table",
              return_value=(procs_fallback, {}),
          ),
          mock.patch.object(
              tmux_yank_id,
              "resolve_cli_conversation_and_dir",
              side_effect=fake_resolve,
          ),
          mock.patch.object(tmux_yank_id, "show_toast"),
      ):
        rc = tmux_yank_id.cmd_tmux_yank_id(cfg, None, [])
      self.assertEqual(rc, 0)
      self.assertEqual(fake_tmux.buffers, ["conv-%1"])

      # Both panes are agy -> active pane %2 wins.
      fake_tmux_both = FakeTmuxRunner(
          panes_out, current_stdout="s1\x1f0\x1f%2\n"
      )
      with (
          mock.patch.object(
              tmux_yank_id, "TmuxRunner", return_value=fake_tmux_both
          ),
          mock.patch.object(
              tmux_yank_id, "load_process_table", return_value=(procs_both, {})
          ),
          mock.patch.object(
              tmux_yank_id,
              "resolve_cli_conversation_and_dir",
              side_effect=fake_resolve,
          ),
          mock.patch.object(tmux_yank_id, "show_toast"),
      ):
        rc = tmux_yank_id.cmd_tmux_yank_id(cfg, None, [])
      self.assertEqual(rc, 0)
      self.assertEqual(fake_tmux_both.buffers, ["conv-%2"])

  def test_non_agent_window_or_missing_id_shows_toast(self) -> None:
    for procs, resolved_id in [
        ({101: {"pid": 101, "ppid": 1, "argv": ["zsh"], "exe": ""}}, None),
        ({101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""}}, None),
    ]:
      fake_tmux = FakeTmuxRunner(
          _make_pane_line(0, "%1", 101, 1), buffers=["existing"]
      )
      with tempfile.TemporaryDirectory() as tmp:
        cfg = self._load_default_config(tmp)
        toast_log = os.path.join(tmp, "toast.log")
        with (
            mock.patch.dict(
                os.environ,
                {
                    "CODE_HARNESS_TMUX_TOAST_LOG": toast_log,
                    "CODE_HARNESS_TMUX_SYNC_TOAST": "1",
                },
            ),
            mock.patch.object(
                tmux_yank_id, "TmuxRunner", return_value=fake_tmux
            ),
            mock.patch.object(
                tmux_yank_id, "load_process_table", return_value=(procs, {})
            ),
            mock.patch.object(
                tmux_yank_id,
                "resolve_cli_conversation_and_dir",
                return_value=(resolved_id, set(), tmp),
            ),
        ):
          rc = tmux_yank_id.cmd_tmux_yank_id(cfg, None, ["/dev/pts/1"])

        self.assertEqual(rc, 0)
        self.assertEqual(fake_tmux.buffers, ["existing"])
        self.assertEqual(
            pathlib.Path(toast_log).read_text(encoding="utf-8").strip(),
            "No conversation ID to yank",
        )

  def test_deduplicates_only_when_latest_buffer_matches(self) -> None:
    id1 = "aaaaaaaa-1111-2222-3333-444444444444"
    id2 = "bbbbbbbb-1111-2222-3333-444444444444"

    # Latest matches id1 -> skip set-buffer.
    fake_same = FakeTmuxRunner(
        _make_pane_line(0, "%1", 101, 1), buffers=[id1, id2]
    )
    self.assertFalse(tmux_yank_id.yank_to_tmux_buffer(fake_same, id1))
    self.assertEqual(fake_same.buffers, [id1, id2])
    self.assertFalse(any(cmd[0] == "set-buffer" for cmd in fake_same.commands))

    # id1 is present, but id2 is latest -> push id1 again.
    fake_older = FakeTmuxRunner(
        _make_pane_line(0, "%1", 101, 1), buffers=[id2, id1]
    )
    self.assertTrue(tmux_yank_id.yank_to_tmux_buffer(fake_older, id1))
    self.assertEqual(fake_older.buffers, [id1, id2, id1])

  def test_cmd_shows_already_yanked_toast_when_latest_matches(self) -> None:
    conv_id = "11111111-2222-3333-4444-555555555555"
    fake_tmux = FakeTmuxRunner(
        _make_pane_line(0, "%1", 101, 1), buffers=[conv_id]
    )
    procs = {101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""}}

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      toast_log = os.path.join(tmp, "toast.log")
      with (
          mock.patch.dict(
              os.environ,
              {
                  "CODE_HARNESS_TMUX_TOAST_LOG": toast_log,
                  "CODE_HARNESS_TMUX_SYNC_TOAST": "1",
              },
          ),
          mock.patch.object(
              tmux_yank_id, "TmuxRunner", return_value=fake_tmux
          ),
          mock.patch.object(
              tmux_yank_id, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_yank_id,
              "resolve_cli_conversation_and_dir",
              return_value=(conv_id, set(), tmp),
          ),
      ):
        rc = tmux_yank_id.cmd_tmux_yank_id(cfg, None, ["/dev/pts/1"])

      self.assertEqual(rc, 0)
      self.assertEqual(fake_tmux.buffers, [conv_id])
      self.assertEqual(
          pathlib.Path(toast_log).read_text(encoding="utf-8").strip(),
          f"Already yanked {conv_id}",
      )

  def test_is_self_script_proc_ignores_tmux_yank_id(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"argv": ["code-harness", "tmux-yank-id", "/dev/pts/1"]}
        )
    )


@unittest.skipUnless(shutil.which("tmux"), "tmux binary not available")
class TmuxYankIdIntegrationTests(unittest.TestCase):

  def test_real_tmux_buffer_stack_dedup_and_reyank(self) -> None:
    id1 = "aaaaaaaa-1111-2222-3333-444444444444"
    id2 = "bbbbbbbb-1111-2222-3333-444444444444"
    with tempfile.TemporaryDirectory() as tmp:
      sock = os.path.join(tmp, "tmux.sock")
      tmux = TmuxRunner(shutil.which("tmux") or "tmux", socket_path=sock)
      tmux.run("new-session", "-d", "-s", "test", "sleep 60")
      try:
        # Empty stack -> yanks id1.
        self.assertTrue(tmux_yank_id.yank_to_tmux_buffer(tmux, id1))
        self.assertEqual(tmux.run("show-buffer").stdout, id1)
        self.assertEqual(
            len(tmux.run("list-buffers").stdout.splitlines()), 1
        )

        # Latest already matches id1 -> no duplicate buffer.
        self.assertFalse(tmux_yank_id.yank_to_tmux_buffer(tmux, id1))
        self.assertEqual(
            len(tmux.run("list-buffers").stdout.splitlines()), 1
        )

        # Push id2 -> becomes latest.
        self.assertTrue(tmux_yank_id.yank_to_tmux_buffer(tmux, id2))
        self.assertEqual(tmux.run("show-buffer").stdout, id2)
        self.assertEqual(
            len(tmux.run("list-buffers").stdout.splitlines()), 2
        )

        # id1 is in an older buffer, not latest -> yanks id1 again.
        self.assertTrue(tmux_yank_id.yank_to_tmux_buffer(tmux, id1))
        self.assertEqual(tmux.run("show-buffer").stdout, id1)
        self.assertEqual(
            len(tmux.run("list-buffers").stdout.splitlines()), 3
        )

        # Latest matches id1 again -> no duplicate.
        self.assertFalse(tmux_yank_id.yank_to_tmux_buffer(tmux, id1))
        self.assertEqual(
            len(tmux.run("list-buffers").stdout.splitlines()), 3
        )
      finally:
        tmux.run("kill-server", check=False)


if __name__ == "__main__":
  unittest.main()

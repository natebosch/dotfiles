"""Unit tests for code_harness.tmux_next hidden subcommand."""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from code_harness import cli, snapshot, tmux_next
from code_harness.config import load_config


def _make_pane_line(
    sess: str,
    widx: int,
    zoomed: int,
    num_panes: int,
    pidx: int,
    pane_id: str,
    pane_pid: int,
    active: int,
) -> str:
  return "\x1f".join(
      str(x)
      for x in (
          sess,
          widx,
          zoomed,
          num_panes,
          pidx,
          pane_id,
          pane_pid,
          active,
      )
  )


class FakeTmuxRunner:

  def __init__(self, panes_stdout: str, current_stdout: str = "") -> None:
    self.base = ["tmux"]
    self.panes_stdout = panes_stdout
    self.current_stdout = current_stdout
    self.commands: list[tuple[str, ...]] = []

  def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    del check
    self.commands.append(args)
    if args and args[0] == "list-panes":
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout=self.panes_stdout, stderr=""
      )
    if args and args[0] == "display-message":
      return subprocess.CompletedProcess(
          [*self.base, *args], 0, stdout=self.current_stdout, stderr=""
      )
    return subprocess.CompletedProcess(
        [*self.base, *args], 0, stdout="", stderr=""
    )


class TmuxNextHiddenCommandTests(unittest.TestCase):

  def _load_default_config(self, tmp: str):
    env = {"XDG_CONFIG_HOME": tmp}
    with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
      return load_config(env=env)

  def test_tmux_next_is_hidden_from_top_level_help(self) -> None:
    self.assertIn("tmux-next", cli.HIDDEN_COMMANDS)
    self.assertNotIn("tmux-next", cli.PUBLIC_COMMANDS)
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertNotIn("tmux-next", stdout.getvalue())

  def test_parse_args_urgent_and_permission_aliases(self) -> None:
    self.assertTrue(tmux_next.parse_args(["--urgent"]).urgent)
    self.assertTrue(tmux_next.parse_args(["-u"]).urgent)
    self.assertTrue(tmux_next.parse_args(["--permission"]).urgent)
    self.assertTrue(tmux_next.parse_args(["-p"]).urgent)
    self.assertFalse(tmux_next.parse_args([]).urgent)

  def test_list_filters_urgent_vs_all_attention_states(self) -> None:
    panes_out = "\n".join([
        _make_pane_line("s1", 0, 0, 1, 0, "%1", 101, 1),
        _make_pane_line("s1", 1, 0, 1, 0, "%2", 102, 1),
        _make_pane_line("s1", 2, 0, 1, 0, "%3", 103, 1),
        _make_pane_line("s1", 3, 0, 1, 0, "%4", 104, 1),
        _make_pane_line("s1", 4, 0, 1, 0, "%5", 105, 1),
    ])
    fake_tmux = FakeTmuxRunner(panes_out)
    procs = {
        101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""},
        102: {"pid": 102, "ppid": 1, "argv": ["agy"], "exe": ""},
        103: {"pid": 103, "ppid": 1, "argv": ["agy"], "exe": ""},
        104: {"pid": 104, "ppid": 1, "argv": ["agy"], "exe": ""},
        105: {"pid": 105, "ppid": 1, "argv": ["agy"], "exe": ""},
    }
    state_by_pane = {
        "%1": (snapshot.CLI_STATE_WORKING, "generating"),
        "%2": (snapshot.CLI_STATE_PERMISSION, "waiting on permission check"),
        "%3": (snapshot.CLI_STATE_QUESTION, "waiting on user question"),
        "%4": (snapshot.CLI_STATE_PLAN, "waiting for plan approval"),
        "%5": (snapshot.CLI_STATE_SETTLED, "waiting for user input"),
    }

    def fake_resolve(cli_info, pane_id, data_dir=None, config=None):
      del cli_info, config
      return f"conv-{pane_id}", set(), data_dir or "/tmp"

    def fake_classify(cli_info, is_print, pane_id, *_args, **_kwargs):
      del cli_info, is_print
      return state_by_pane[pane_id]

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      with (
          mock.patch.object(tmux_next, "TmuxRunner", return_value=fake_tmux),
          mock.patch.object(
              tmux_next, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_next, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_next, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_next,
              "resolve_cli_conversation_and_dir",
              side_effect=fake_resolve,
          ),
          mock.patch.object(
              tmux_next, "classify_cli_state", side_effect=fake_classify
          ),
      ):
        out_all = io.StringIO()
        with mock.patch("sys.stdout", out_all):
          rc = tmux_next.cmd_tmux_next(cfg, None, ["--list"])
        self.assertEqual(rc, 0)
        lines_all = out_all.getvalue().splitlines()
        self.assertEqual(len(lines_all), 4)
        self.assertIn("%2\tpermission\tconv-%2", lines_all[0])
        self.assertIn("%3\tquestion\tconv-%3", lines_all[1])
        self.assertIn("%4\tplan\tconv-%4", lines_all[2])
        self.assertIn("%5\tsettled\tconv-%5", lines_all[3])

        out_urgent = io.StringIO()
        with mock.patch("sys.stdout", out_urgent):
          rc = tmux_next.cmd_tmux_next(cfg, None, ["--urgent", "--list"])
        self.assertEqual(rc, 0)
        lines_urgent = out_urgent.getvalue().splitlines()
        self.assertEqual(len(lines_urgent), 2)
        self.assertIn("%2\tpermission\tconv-%2", lines_urgent[0])
        self.assertIn("%3\tquestion\tconv-%3", lines_urgent[1])

        out_perm = io.StringIO()
        with mock.patch("sys.stdout", out_perm):
          rc = tmux_next.cmd_tmux_next(cfg, None, ["--permission", "--list"])
        self.assertEqual(rc, 0)
        self.assertEqual(out_perm.getvalue().splitlines(), lines_urgent)

  def test_cycles_wrapping_unzooms_and_switches_session(self) -> None:
    panes_out = "\n".join([
        _make_pane_line("s1", 0, 1, 2, 0, "%1", 101, 1),
        _make_pane_line("s1", 0, 1, 2, 1, "%2", 102, 0),
        _make_pane_line("s2", 1, 0, 1, 0, "%3", 103, 1),
    ])
    fake_tmux = FakeTmuxRunner(panes_out, current_stdout="s2\x1f1\x1f%3\n")
    procs = {
        101: {"pid": 101, "ppid": 1, "argv": ["zsh"], "exe": ""},
        102: {"pid": 102, "ppid": 1, "argv": ["agy"], "exe": ""},
        103: {"pid": 103, "ppid": 1, "argv": ["agy"], "exe": ""},
    }

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      with (
          mock.patch.object(tmux_next, "TmuxRunner", return_value=fake_tmux),
          mock.patch.object(
              tmux_next, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_next, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_next, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_next,
              "resolve_cli_conversation_and_dir",
              return_value=(None, set(), tmp),
          ),
          mock.patch.object(
              tmux_next,
              "classify_cli_state",
              return_value=(snapshot.CLI_STATE_PERMISSION, "perm"),
          ),
      ):
        rc = tmux_next.cmd_tmux_next(cfg, None, ["--permission", "/dev/pts/9"])
    self.assertEqual(rc, 0)
    self.assertEqual(
        fake_tmux.commands[-1],
        (
            "select-window",
            "-t",
            "=s1:0",
            ";",
            "resize-pane",
            "-Z",
            "-t",
            "=s1:0",
            ";",
            "select-pane",
            "-t",
            "%2",
            ";",
            "switch-client",
            "-c",
            "/dev/pts/9",
            "-t",
            "=s1",
        ),
    )

  def test_same_window_cycles_panes_and_shows_toast_when_alone(self) -> None:
    panes_out = "\n".join([
        _make_pane_line("s1", 0, 0, 2, 0, "%1", 101, 1),
        _make_pane_line("s1", 0, 0, 2, 1, "%2", 102, 0),
    ])
    procs = {
        101: {"pid": 101, "ppid": 1, "argv": ["agy"], "exe": ""},
        102: {"pid": 102, "ppid": 1, "argv": ["agy"], "exe": ""},
    }

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      toast_log = os.path.join(tmp, "toast.log")

      # Two matching panes in current window -> cycles to %2.
      fake_tmux = FakeTmuxRunner(panes_out, current_stdout="s1\x1f0\x1f%1\n")
      with (
          mock.patch.object(tmux_next, "TmuxRunner", return_value=fake_tmux),
          mock.patch.object(
              tmux_next, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_next, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_next, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_next,
              "resolve_cli_conversation_and_dir",
              return_value=(None, set(), tmp),
          ),
          mock.patch.object(
              tmux_next,
              "classify_cli_state",
              return_value=(snapshot.CLI_STATE_SETTLED, "idle"),
          ),
      ):
        rc = tmux_next.cmd_tmux_next(cfg, None, [])
      self.assertEqual(rc, 0)
      self.assertEqual(fake_tmux.commands[-1], ("select-pane", "-t", "%2"))

      # Only current pane matches -> shows no-other toast.
      fake_tmux_alone = FakeTmuxRunner(
          _make_pane_line("s1", 0, 0, 1, 0, "%1", 101, 1),
          current_stdout="s1\x1f0\x1f%1\n",
      )
      with (
          mock.patch.dict(
              os.environ,
              {
                  "CODE_HARNESS_TMUX_TOAST_LOG": toast_log,
                  "CODE_HARNESS_TMUX_SYNC_TOAST": "1",
              },
          ),
          mock.patch.object(
              tmux_next, "TmuxRunner", return_value=fake_tmux_alone
          ),
          mock.patch.object(
              tmux_next, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_next, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_next, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_next,
              "resolve_cli_conversation_and_dir",
              return_value=(None, set(), tmp),
          ),
          mock.patch.object(
              tmux_next,
              "classify_cli_state",
              return_value=(snapshot.CLI_STATE_PERMISSION, "perm"),
          ),
      ):
        rc = tmux_next.cmd_tmux_next(cfg, None, ["--permission", "/dev/pts/1"])
      self.assertEqual(rc, 0)
      self.assertEqual(
          pathlib.Path(toast_log).read_text(encoding="utf-8").strip(),
          "No other agent needing urgent attention",
      )
      self.assertEqual(fake_tmux_alone.commands[-1][0], "display-popup")

      # No pane matches -> shows empty toast.
      fake_tmux_none = FakeTmuxRunner(
          _make_pane_line("s1", 0, 0, 1, 0, "%1", 101, 1),
          current_stdout="s1\x1f0\x1f%1\n",
      )
      with (
          mock.patch.dict(
              os.environ,
              {
                  "CODE_HARNESS_TMUX_TOAST_LOG": toast_log,
                  "CODE_HARNESS_TMUX_SYNC_TOAST": "1",
              },
          ),
          mock.patch.object(
              tmux_next, "TmuxRunner", return_value=fake_tmux_none
          ),
          mock.patch.object(
              tmux_next, "load_process_table", return_value=(procs, {})
          ),
          mock.patch.object(
              tmux_next, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_next, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_next,
              "resolve_cli_conversation_and_dir",
              return_value=(None, set(), tmp),
          ),
          mock.patch.object(
              tmux_next,
              "classify_cli_state",
              return_value=(snapshot.CLI_STATE_WORKING, "busy"),
          ),
      ):
        rc = tmux_next.cmd_tmux_next(cfg, None, [])
      self.assertEqual(rc, 0)
      lines = pathlib.Path(toast_log).read_text(encoding="utf-8").splitlines()
      self.assertEqual(lines[-1], "No agent waiting for input")


if __name__ == "__main__":
  unittest.main()

"""Unit and tmux integration tests for code_harness.tmux_editor."""

from __future__ import annotations

import io
import json
import os
import pathlib
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from code_harness import cli, snapshot, tmux_editor
from code_harness.config import load_config

SRC_DIR = str(pathlib.Path(__file__).resolve().parents[1] / "src")


def _write_exec(path: str, content: str) -> None:
  with open(path, "w", encoding="utf-8") as f:
    f.write(textwrap.dedent(content).lstrip("\n"))
  os.chmod(path, 0o755)


def _wait_until(pred, timeout: float = 8.0, interval: float = 0.02, desc: str = "condition"):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    val = pred()
    if val:
      return val
    time.sleep(interval)
  raise AssertionError(f"Timed out waiting for {desc}")


def _read_text(path: str) -> str:
  if not os.path.exists(path):
    return ""
  with open(path, encoding="utf-8", errors="replace") as f:
    return f.read()


def _create_conv_db(data_dir: str, conv_id: str, step_type: int, status: int) -> str:
  conv_dir = os.path.join(data_dir, "conversations")
  pres_dir = os.path.join(data_dir, "presence")
  os.makedirs(conv_dir, exist_ok=True)
  os.makedirs(pres_dir, exist_ok=True)
  db_path = os.path.join(conv_dir, f"{conv_id}.db")
  con = sqlite3.connect(db_path)
  try:
    con.execute(
        "CREATE TABLE IF NOT EXISTS steps ("
        "idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, "
        "task_details BLOB, step_payload BLOB, metadata BLOB)"
    )
    con.execute("DELETE FROM steps")
    con.execute(
        "INSERT INTO steps (idx, step_type, status) VALUES (1, ?, ?)",
        (step_type, status),
    )
    con.commit()
  finally:
    con.close()
  lock_path = os.path.join(pres_dir, f"{conv_id}.lock")
  open(lock_path, "a", encoding="utf-8").close()
  return lock_path


FAKE_CLI_SCRIPT = """
#!/usr/bin/env python3
import os
import shlex
import subprocess
import sys
import tempfile
import termios
import tty


def log_event(path, msg):
  with open(path, "a", encoding="utf-8") as f:
    f.write(msg + "\\n")
    f.flush()


def main():
  editor = shlex.split(os.environ["FAKE_CLI_EDITOR"])
  prompt_file = os.environ["FAKE_CLI_PROMPT"]
  events_log = os.environ["FAKE_CLI_EVENTS"]
  tmp_dir = os.environ["FAKE_CLI_TMP"]
  size = os.environ.get("FAKE_CLI_SIZE", "")
  lock_file = os.environ.get("FAKE_CLI_LOCK", "")
  ignore_cg_flag = os.environ.get("FAKE_CLI_IGNORE_CG_FLAG", "")
  exit_after_pass1_flag = os.environ.get(
      "FAKE_CLI_EXIT_AFTER_PASS1_FLAG", ""
  )

  lock_fd = None
  if lock_file:
    lock_fd = open(lock_file, "rb")

  fd = sys.stdin.fileno()
  old_attr = termios.tcgetattr(fd)
  seq = 0
  try:
    tty.setcbreak(fd)
    log_event(events_log, "READY")
    while True:
      ch = os.read(fd, 1)
      if not ch:
        break
      if ch == b"\\x07":
        if ignore_cg_flag and os.path.exists(ignore_cg_flag):
          log_event(events_log, "IGNORED_CG")
          continue
        seq += 1
        with open(prompt_file, "rb") as pf:
          cur = pf.read()
        tfd, tpath = tempfile.mkstemp(
            prefix="agy-prompt-", suffix=".txt", dir=tmp_dir
        )
        with os.fdopen(tfd, "wb") as tf:
          tf.write(cur)
        cmd = [*editor]
        if size:
          cmd.extend(["--size", size])
        cmd.append(tpath)
        res = subprocess.run(cmd, check=False)
        with open(tpath, "rb") as tf:
          updated = tf.read().decode("utf-8", "replace").rstrip("\\n")
        os.unlink(tpath)
        with open(prompt_file, "w", encoding="utf-8") as pf:
          pf.write(updated)
        log_event(events_log, f"CG:{seq}:{res.returncode}:{updated}")
        if exit_after_pass1_flag and os.path.exists(
            exit_after_pass1_flag
        ):
          log_event(events_log, "EXITED_AFTER_PASS1")
          break
      elif ch == b"p":
        log_event(events_log, "ALIVE")
      elif ch == b"q":
        log_event(events_log, "QUIT")
        break
  finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
    if lock_fd is not None:
      lock_fd.close()


if __name__ == "__main__":
  sys.exit(main())
"""


class UnitHelpersTest(unittest.TestCase):

  def _default_config(self, tmp: str):
    env = {"XDG_CONFIG_HOME": tmp}
    with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
      return load_config(env=env)

  def test_tmux_editor_is_hidden_from_top_level_help(self) -> None:
    self.assertIn("tmux-editor", cli.HIDDEN_COMMANDS)
    self.assertNotIn("tmux-editor", cli.PUBLIC_COMMANDS)
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertNotIn("tmux-editor", stdout.getvalue())

  def test_is_prompt_file_default_and_custom_flavor(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._default_config(tmp)
      self.assertTrue(
          tmux_editor.is_prompt_file("/tmp/agy-prompt-12345.txt", config=cfg, env={})
      )
      self.assertTrue(
          tmux_editor.is_prompt_file("antigravity-prompt-.txt", config=cfg, env={})
      )
      self.assertFalse(
          tmux_editor.is_prompt_file(
              "/home/u/.gemini/keybindings.json", config=cfg, env={}
          )
      )
      self.assertFalse(
          tmux_editor.is_prompt_file("/tmp/plan.md", config=cfg, env={})
      )
      self.assertFalse(
          tmux_editor.is_prompt_file("/tmp/agy-prompt-123.md", config=cfg, env={})
      )

      # Env override supports custom globs.
      self.assertTrue(
          tmux_editor.is_prompt_file(
              "/tmp/custom-prompt-9.txt",
              config=cfg,
              env={"CODE_HARNESS_TMUX_EDITOR_PROMPT_GLOB": "custom-prompt-*.txt"},
          )
      )

      # Declarative flavor config supports custom prompt_file_globs.
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({
              "flavors": {
                  "alt": {
                      "prompt_file_globs": ["alt-prompt-*.txt"],
                  },
              },
          }),
          encoding="utf-8",
      )
      custom_cfg = load_config(config_path=cfg_file)
      self.assertTrue(
          tmux_editor.is_prompt_file(
              "/tmp/alt-prompt-abc.txt", config=custom_cfg, env={}
          )
      )

  def test_make_pane_key_separates_sockets(self) -> None:
    k1 = tmux_editor.make_pane_key("/tmp/sock-a", "%0")
    k2 = tmux_editor.make_pane_key("/tmp/sock-b", "%0")
    self.assertNotEqual(k1, k2)

  def test_resolve_vim_skips_self_and_nested(self) -> None:
    env = {
        "VISUAL": "code-harness tmux-editor",
        "EDITOR": "edit_nested",
    }
    cmd = tmux_editor.resolve_vim_command(env=env)
    self.assertNotIn("code-harness", cmd[0])
    self.assertNotIn("edit_nested", cmd[0])

    env2 = {
        "VISUAL": "/usr/local/bin/code-harness --flavor alt tmux-editor",
        "EDITOR": "/usr/bin/vim -n",
    }
    self.assertEqual(
        tmux_editor.resolve_vim_command(env=env2), ["/usr/bin/vim", "-n"]
    )

    env3 = {
        "CODE_HARNESS_TMUX_EDITOR_VIM": "vim -u NONE",
        "EDITOR": "/usr/bin/vim -n",
    }
    self.assertEqual(
        tmux_editor.resolve_vim_command(env=env3), ["vim", "-u", "NONE"]
    )

  def test_outside_tmux_execs_vim_directly(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      fake_vim = os.path.join(tmp, "fake_vim")
      args_log = os.path.join(tmp, "vim_args.json")
      _write_exec(
          fake_vim,
          f"""
          #!{sys.executable}
          import json, sys
          with open({args_log!r}, "w") as f:
            json.dump(sys.argv[1:], f)
          """,
      )
      target = os.path.join(tmp, "agy-prompt-1.txt")
      open(target, "w", encoding="utf-8").close()
      cfg_file = os.path.join(tmp, "config.json")
      with open(cfg_file, "w", encoding="utf-8") as f:
        f.write("{}")
      env = os.environ.copy()
      env.pop("TMUX", None)
      env.pop("TMUX_PANE", None)
      env["CODE_HARNESS_CONFIG"] = cfg_file
      env["CODE_HARNESS_TMUX_EDITOR_VIM"] = fake_vim
      env["PYTHONPATH"] = SRC_DIR
      res = subprocess.run(
          [
              sys.executable,
              "-m",
              "code_harness.cli",
              "--builtin",
              "tmux-editor",
              "+12",
              target,
          ],
          env=env,
          check=False,
      )
      self.assertEqual(res.returncode, 0)
      with open(args_log, encoding="utf-8") as f:
        self.assertEqual(json.load(f), ["+12", target])

  def test_snapshot_ignores_tmux_editor(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc({
            "comm": "code-harness",
            "argv": ["code-harness", "tmux-editor", "--apply-worker"],
        })
    )
    self.assertTrue(
        snapshot.is_self_script_proc({
            "comm": "python3",
            "argv": [
                "python3",
                "-m",
                "code_harness.cli",
                "tmux-editor",
                "--split-worker",
            ],
        })
    )


class TmuxIntegrationTest(unittest.TestCase):

  def setUp(self) -> None:
    self.tmux_bin = self._find_tmux()
    if not self.tmux_bin:
      self.skipTest("tmux not available")

    self.tmp = tempfile.mkdtemp(prefix="code-harness-editor-test-")
    self.addCleanup(shutil.rmtree, self.tmp, True)

    self.sock = os.path.join(self.tmp, "tmux.sock")
    self.state_dir = os.path.join(self.tmp, "state")
    self.toast_log = os.path.join(self.tmp, "toasts.log")
    self.data_dir = os.path.join(self.tmp, "agent_data")
    self.config_file = os.path.join(self.tmp, "config.json")
    os.makedirs(self.state_dir, mode=0o700)
    os.makedirs(self.data_dir)
    with open(self.config_file, "w", encoding="utf-8") as f:
      f.write("{}")

    self.addCleanup(self._kill_tmux)

    self.fake_cli = os.path.join(self.tmp, "agy")
    _write_exec(self.fake_cli, FAKE_CLI_SCRIPT)
    self.editor_cmd = [
        sys.executable,
        "-m",
        "code_harness.cli",
        "--builtin",
        "tmux-editor",
    ]

  def _find_tmux(self) -> str | None:
    nix = os.path.expanduser("~/.nix-profile/bin/tmux")
    if os.access(nix, os.X_OK):
      return nix
    return shutil.which("tmux")

  def _tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    assert self.tmux_bin is not None
    return subprocess.run(
        [self.tmux_bin, "-S", self.sock, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=check,
    )

  def _kill_tmux(self) -> None:
    if os.path.exists(self.sock):
      self._tmux("kill-server", check=False)

  def _list_panes(self) -> list[dict]:
    res = self._tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\x1f#{pane_active}\x1f#{pane_left}\x1f#{pane_width}",
        check=False,
    )
    if res.returncode != 0:
      return []
    out = []
    for line in res.stdout.splitlines():
      pid, act, left, width = line.split("\x1f")
      out.append({
          "pane_id": pid,
          "active": act == "1",
          "left": int(left),
          "width": int(width),
      })
    return out

  def _base_env(self, vim_cmd: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC_DIR
    env["CODE_HARNESS_CONFIG"] = self.config_file
    env["CODE_HARNESS_TMUX_EDITOR_VIM"] = vim_cmd
    env["CODE_HARNESS_TMUX_EDITOR_STATE_DIR"] = self.state_dir
    env["CODE_HARNESS_TMUX_TOAST_LOG"] = self.toast_log
    env["CODE_HARNESS_TMUX_SYNC_TOAST"] = "1"
    env["CODE_HARNESS_DATA_DIR"] = self.data_dir
    return env

  def _start_cli_session(
      self,
      initial_prompt: str,
      vim_cmd: str,
      size: str = "50%",
      lock_file: str = "",
      ignore_cg_flag: str = "",
      exit_after_pass1_flag: str = "",
      force_send: bool = False,
      shell_wrapper: bool = False,
  ) -> tuple[str, str, str]:
    prompt_file = os.path.join(self.tmp, "prompt.txt")
    events_log = os.path.join(self.tmp, "events.log")
    with open(prompt_file, "w", encoding="utf-8") as f:
      f.write(initial_prompt)

    cli_args = [self.fake_cli]

    env_pairs = [
        ("PYTHONPATH", SRC_DIR),
        ("CODE_HARNESS_CONFIG", self.config_file),
        ("CODE_HARNESS_TMUX_EDITOR_VIM", vim_cmd),
        ("CODE_HARNESS_TMUX_EDITOR_STATE_DIR", self.state_dir),
        ("CODE_HARNESS_TMUX_TOAST_LOG", self.toast_log),
        ("CODE_HARNESS_TMUX_SYNC_TOAST", "1"),
        ("CODE_HARNESS_DATA_DIR", self.data_dir),
        ("FAKE_CLI_EDITOR", shlex.join(self.editor_cmd)),
        ("FAKE_CLI_PROMPT", prompt_file),
        ("FAKE_CLI_EVENTS", events_log),
        ("FAKE_CLI_TMP", self.tmp),
    ]
    if size:
      env_pairs.append(("FAKE_CLI_SIZE", size))
    if lock_file:
      env_pairs.append(("FAKE_CLI_LOCK", lock_file))
    if ignore_cg_flag:
      env_pairs.append(("FAKE_CLI_IGNORE_CG_FLAG", ignore_cg_flag))
    if exit_after_pass1_flag:
      env_pairs.append(
          ("FAKE_CLI_EXIT_AFTER_PASS1_FLAG", exit_after_pass1_flag)
      )
    if force_send:
      env_pairs.append(("CODE_HARNESS_TMUX_EDITOR_FORCE_SEND", "1"))

    cmd_args = [
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-x",
        "160",
        "-y",
        "40",
        "-s",
        "test",
        "-n",
        "main",
    ]
    for k, v in env_pairs:
      cmd_args.extend(["-e", f"{k}={v}"])

    if shell_wrapper:
      cmd_args.append("bash --norc --noprofile")
      self._tmux(*cmd_args)
      panes = self._list_panes()
      orig_pane = panes[0]["pane_id"]
      self._tmux("send-keys", "-t", orig_pane, "-l", shlex.join(cli_args))
      self._tmux("send-keys", "-t", orig_pane, "Enter")
    else:
      cmd_args.append(shlex.join(cli_args))
      self._tmux(*cmd_args)
      panes = self._list_panes()
      orig_pane = panes[0]["pane_id"]

    _wait_until(
        lambda: "READY" in _read_text(events_log),
        desc="fake cli READY",
    )
    return orig_pane, prompt_file, events_log

  def _real_vim_cmd(self) -> str:
    nix_vim = os.path.expanduser("~/.nix-profile/bin/vim")
    vim_bin = nix_vim if os.access(nix_vim, os.X_OK) else shutil.which("vim")
    if not vim_bin:
      self.skipTest("vim not available")
    return f"{vim_bin} -N -u NONE -n -i NONE"

  def test_full_two_stage_round_trip_real_vim_and_live_left_pane(self) -> None:
    conv_id = "11111111-1111-1111-1111-111111111111"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "hello",
        self._real_vim_cmd(),
        size="50%",
        lock_file=lock_file,
    )

    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:1:0:hello" in _read_text(events_log),
        desc="Pass 1 completion",
    )
    _wait_until(
        lambda: len(self._list_panes()) == 2,
        desc="two side-by-side panes",
    )
    panes = self._list_panes()
    left_pane, right_pane = sorted(panes, key=lambda p: p["left"])
    self.assertEqual(left_pane["pane_id"], orig_pane)
    self.assertTrue(right_pane["active"])
    self.assertGreater(right_pane["left"], left_pane["left"])
    self.assertEqual(right_pane["width"], 80)

    # Left pane remains live while the split editor is open.
    self._tmux("send-keys", "-t", orig_pane, "p")
    _wait_until(
        lambda: "ALIVE" in _read_text(events_log),
        desc="left pane alive during split",
    )

    # Wait for Vim to load the seeded draft before sending keys.
    _wait_until(
        lambda: "hello" in self._tmux(
            "capture-pane", "-p", "-t", right_pane["pane_id"]
        ).stdout,
        desc="vim buffer showing hello",
    )
    self._tmux("send-keys", "-t", right_pane["pane_id"], "A world", "Escape")
    self._tmux("send-keys", "-t", right_pane["pane_id"], ":wq", "Enter")

    _wait_until(
        lambda: "CG:2:0:hello world" in _read_text(events_log),
        desc="Pass 2 applied hello world",
    )
    _wait_until(
        lambda: len(self._list_panes()) == 1,
        desc="split pane closed",
    )
    self.assertEqual(_read_text(prompt_file), "hello world")

  def test_cancel_and_unchanged_paths_skip_pass2(self) -> None:
    conv_id = "22222222-2222-2222-2222-222222222222"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)

    # 1. :cq (non-zero exit) keeps prompt unchanged and skips Pass 2.
    cq_vim = os.path.join(self.tmp, "cq_vim.py")
    _write_exec(
        cq_vim,
        """
        #!/usr/bin/env python3
        import sys
        with open(sys.argv[-1], "w") as f:
          f.write("aborted edit\\n")
        sys.exit(1)
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "keep me", cq_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:1:0:keep me" in _read_text(events_log),
        desc="Pass 1 for :cq",
    )
    _wait_until(lambda: len(self._list_panes()) == 1, desc="pane close :cq")
    time.sleep(0.25)
    self.assertNotIn("CG:2:", _read_text(events_log))
    self.assertEqual(_read_text(prompt_file), "keep me")
    leftover = [
        name
        for name in os.listdir(self.state_dir)
        if not name.endswith(".lock")
    ]
    self.assertEqual(leftover, [])
    self._kill_tmux()

    # 2. :q! without saving skips Pass 2.
    qbang_vim = os.path.join(self.tmp, "qbang_vim.py")
    _write_exec(
        qbang_vim,
        """
        #!/usr/bin/env python3
        import sys
        sys.exit(0)
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "keep me", qbang_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:1:0:keep me" in _read_text(events_log),
        desc="Pass 1 for :q!",
    )
    _wait_until(lambda: len(self._list_panes()) == 1, desc="pane close :q!")
    time.sleep(0.25)
    self.assertNotIn("CG:2:", _read_text(events_log))
    self.assertEqual(_read_text(prompt_file), "keep me")
    self._kill_tmux()

    # 3. Empty :wq on an empty prompt skips Pass 2.
    empty_wq_vim = os.path.join(self.tmp, "empty_wq_vim.py")
    _write_exec(
        empty_wq_vim,
        """
        #!/usr/bin/env python3
        import os, sys
        with open(sys.argv[-1], "w") as f:
          f.write("\\n")
        os.utime(sys.argv[-1], None)
        sys.exit(0)
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "", empty_wq_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:1:0:" in _read_text(events_log),
        desc="Pass 1 for empty :wq",
    )
    _wait_until(
        lambda: len(self._list_panes()) == 1, desc="pane close empty :wq"
    )
    time.sleep(0.25)
    self.assertNotIn("CG:2:", _read_text(events_log))
    self.assertEqual(_read_text(prompt_file), "")

  def test_clearing_non_empty_prompt_updates_to_empty(self) -> None:
    conv_id = "33333333-3333-3333-3333-333333333333"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "erase this prompt",
        self._real_vim_cmd(),
        lock_file=lock_file,
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    panes = _wait_until(
        lambda: self._list_panes() if len(self._list_panes()) == 2 else None,
        desc="two panes for clear test",
    )
    right_pane = max(panes, key=lambda p: p["left"])
    _wait_until(
        lambda: "erase this prompt" in self._tmux(
            "capture-pane", "-p", "-t", right_pane["pane_id"]
        ).stdout,
        desc="vim buffer showing erase text",
    )
    self._tmux("send-keys", "-t", right_pane["pane_id"], ":%d", "Enter")
    self._tmux("send-keys", "-t", right_pane["pane_id"], ":wq", "Enter")
    _wait_until(
        lambda: "CG:2:0:" in _read_text(events_log),
        desc="Pass 2 cleared prompt",
    )
    self.assertEqual(_read_text(prompt_file), "")

  def test_second_ctrl_g_focuses_existing_split_without_third_pane(self) -> None:
    conv_id = "44444444-4444-4444-4444-444444444444"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    gate_opened = os.path.join(self.tmp, "gate_opened")
    gate_proceed = os.path.join(self.tmp, "gate_proceed")
    gated_vim = os.path.join(self.tmp, "gated_vim.py")
    _write_exec(
        gated_vim,
        f"""
        #!/usr/bin/env python3
        import os, sys, time
        open({gate_opened!r}, "w").close()
        while not os.path.exists({gate_proceed!r}):
          time.sleep(0.02)
        with open(sys.argv[-1], "w") as f:
          f.write("draft edited\\n")
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "draft", gated_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(lambda: os.path.exists(gate_opened), desc="gated vim open")
    panes = _wait_until(
        lambda: self._list_panes() if len(self._list_panes()) == 2 else None,
        desc="two panes",
    )
    editor_pane = next(p["pane_id"] for p in panes if p["pane_id"] != orig_pane)

    # Switch focus back to left pane, then press C-g again.
    self._tmux("select-pane", "-t", orig_pane)
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:2:0:draft" in _read_text(events_log),
        desc="second C-g Pass 1 focus",
    )
    panes_after = self._list_panes()
    self.assertEqual(len(panes_after), 2)
    active_pane = next(p["pane_id"] for p in panes_after if p["active"])
    self.assertEqual(active_pane, editor_pane)
    self.assertEqual(_read_text(prompt_file), "draft")

    open(gate_proceed, "w").close()
    _wait_until(
        lambda: "CG:3:0:draft edited" in _read_text(events_log),
        desc="final Pass 2 apply",
    )
    self.assertEqual(_read_text(prompt_file), "draft edited")

  def test_blocking_split_for_non_prompt_file_and_exit_codes(self) -> None:
    self._tmux(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-x",
        "160",
        "-y",
        "40",
        "-s",
        "blk",
        "-n",
        "main",
        "sleep 30",
    )
    orig_pane = self._list_panes()[0]["pane_id"]
    keybindings_file = os.path.join(self.tmp, "keybindings.json")
    with open(keybindings_file, "w", encoding="utf-8") as f:
      f.write("{}\n")

    vim_started = os.path.join(self.tmp, "blk_started")
    vim_proceed = os.path.join(self.tmp, "blk_proceed")
    args_out = os.path.join(self.tmp, "blk_args.json")
    blk_vim = os.path.join(self.tmp, "blk_vim.py")
    _write_exec(
        blk_vim,
        f"""
        #!/usr/bin/env python3
        import json, os, sys, time
        with open({args_out!r}, "w") as f:
          json.dump(sys.argv[1:], f)
        open({vim_started!r}, "w").close()
        while not os.path.exists({vim_proceed!r}):
          time.sleep(0.02)
        with open(sys.argv[-1], "w") as f:
          f.write('{{"cli.exit": ["ctrl+d"]}}\\n')
        rc = int(os.environ.get("TEST_BLK_RC", "0"))
        sys.exit(rc)
        """,
    )
    env = self._base_env(blk_vim)
    env["TMUX"] = f"{self.sock},1,0"
    env["TMUX_PANE"] = orig_pane

    proc = subprocess.Popen(
        [*self.editor_cmd, "+3", keybindings_file], env=env
    )
    _wait_until(lambda: os.path.exists(vim_started), desc="blocking vim start")
    self.assertIsNone(proc.poll())
    self.assertEqual(len(self._list_panes()), 2)

    open(vim_proceed, "w").close()
    self.assertEqual(proc.wait(timeout=5.0), 0)
    self.assertEqual(len(self._list_panes()), 1)
    with open(args_out, encoding="utf-8") as f:
      recorded = json.load(f)
    self.assertEqual(recorded[0], "+3")
    self.assertIn('"cli.exit"', _read_text(keybindings_file))

    # Verify non-zero exit code propagation.
    os.unlink(vim_started)
    self._tmux("set-environment", "-g", "TEST_BLK_RC", "3")
    proc2 = subprocess.Popen([*self.editor_cmd, keybindings_file], env=env)
    self.assertEqual(proc2.wait(timeout=5.0), 3)

    # Verify killing the split pane unblocks promptly with exit code 1.
    os.unlink(vim_started)
    os.unlink(vim_proceed)
    self._tmux("set-environment", "-u", "-g", "TEST_BLK_RC")
    proc3 = subprocess.Popen([*self.editor_cmd, keybindings_file], env=env)
    _wait_until(lambda: os.path.exists(vim_started), desc="pane kill start")
    panes = self._list_panes()
    split_id = next(p["pane_id"] for p in panes if p["pane_id"] != orig_pane)
    self._tmux("kill-pane", "-t", split_id)
    self.assertEqual(proc3.wait(timeout=5.0), 1)

  def test_permission_modal_keeps_staged_and_loads_buffer(self) -> None:
    conv_id = "55555555-5555-5555-5555-555555555555"
    # step_type=132 (GENERIC), status=9 (STATUS_WAITING) -> CLI_STATE_PERMISSION
    lock_file = _create_conv_db(self.data_dir, conv_id, 132, 9)
    edit_vim = os.path.join(self.tmp, "edit_vim.py")
    _write_exec(
        edit_vim,
        """
        #!/usr/bin/env python3
        import sys
        with open(sys.argv[-1], "w") as f:
          f.write("staged while modal open\\n")
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "initial", edit_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:1:0:initial" in _read_text(events_log),
        desc="Pass 1 permission modal test",
    )
    _wait_until(
        lambda: "Prompt staged: dismiss modal then press C-g"
        in _read_text(self.toast_log),
        desc="permission modal toast",
    )
    self.assertNotIn("CG:2:", _read_text(events_log))
    self.assertEqual(_read_text(prompt_file), "initial")

    # Tmux buffer holds the prompt, and ready file stays staged.
    buf = self._tmux("show-buffer").stdout
    self.assertEqual(buf, "staged while modal open\n")
    ready_files = [
        f for f in os.listdir(self.state_dir) if f.endswith(".ready")
    ]
    self.assertEqual(len(ready_files), 1)

    # Dismiss modal (settled) and press C-g -> Pass 2 applies staged prompt.
    _create_conv_db(self.data_dir, conv_id, 15, 3)
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:2:0:staged while modal open" in _read_text(events_log),
        desc="manual Pass 2 after modal dismissal",
    )
    self.assertEqual(_read_text(prompt_file), "staged while modal open")

  def test_exited_agent_drops_ready_and_saves_to_buffer(self) -> None:
    conv_id = "66666666-6666-6666-6666-666666666666"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    exit_flag = os.path.join(self.tmp, "exit_after_pass1")
    open(exit_flag, "w", encoding="utf-8").close()

    gate_opened = os.path.join(self.tmp, "exit_gate_opened")
    gate_proceed = os.path.join(self.tmp, "exit_gate_proceed")
    gated_vim = os.path.join(self.tmp, "exit_gated_vim.py")
    _write_exec(
        gated_vim,
        f"""
        #!/usr/bin/env python3
        import os, sys, time
        open({gate_opened!r}, "w").close()
        while not os.path.exists({gate_proceed!r}):
          time.sleep(0.02)
        with open(sys.argv[-1], "w") as f:
          f.write("saved after exit\\n")
        """,
    )
    orig_pane, _, events_log = self._start_cli_session(
        "before exit",
        gated_vim,
        lock_file=lock_file,
        exit_after_pass1_flag=exit_flag,
        shell_wrapper=True,
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "EXITED_AFTER_PASS1" in _read_text(events_log),
        desc="fake cli exited to bash",
    )
    _wait_until(lambda: os.path.exists(gate_opened), desc="split open")
    # Wait briefly for bash to reap fake cli before vim closes.
    time.sleep(0.15)
    open(gate_proceed, "w", encoding="utf-8").close()

    _wait_until(
        lambda: "Agent exited; saved prompt to tmux buffer"
        in _read_text(self.toast_log),
        desc="exited agent toast",
    )
    self.assertEqual(self._tmux("show-buffer").stdout, "saved after exit\n")
    ready_files = [
        f for f in os.listdir(self.state_dir) if f.endswith(".ready")
    ]
    self.assertEqual(ready_files, [])

  def test_unconsumed_cg_stages_and_notifies_overlay(self) -> None:
    conv_id = "77777777-7777-7777-7777-777777777777"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    ignore_flag = os.path.join(self.tmp, "ignore_cg")
    gate_flag = os.path.join(self.tmp, "overlay_gate")
    gated_vim = os.path.join(self.tmp, "overlay_gated_vim.py")
    _write_exec(
        gated_vim,
        f"""
        #!/usr/bin/env python3
        import os, sys, time
        open({ignore_flag!r}, "w").close()
        while not os.path.exists({gate_flag!r}):
          time.sleep(0.02)
        with open(sys.argv[-1], "w") as f:
          f.write("staged for overlay\\n")
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "orig",
        gated_vim,
        lock_file=lock_file,
        ignore_cg_flag=ignore_flag,
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: os.path.exists(ignore_flag), desc="overlay ignore_cg armed"
    )
    open(gate_flag, "w", encoding="utf-8").close()

    _wait_until(
        lambda: "Prompt staged: close panel then press C-g"
        in _read_text(self.toast_log),
        desc="unconsumed C-g toast",
    )
    self.assertIn("IGNORED_CG", _read_text(events_log))
    self.assertEqual(self._tmux("show-buffer").stdout, "staged for overlay\n")
    ready_files = [
        f for f in os.listdir(self.state_dir) if f.endswith(".ready")
    ]
    self.assertEqual(len(ready_files), 1)
    self.assertEqual(_read_text(prompt_file), "orig")

  def test_copy_mode_cleared_before_sending_cg(self) -> None:
    conv_id = "88888888-8888-8888-8888-888888888888"
    lock_file = _create_conv_db(self.data_dir, conv_id, 15, 3)
    gate_opened = os.path.join(self.tmp, "cm_opened")
    gate_proceed = os.path.join(self.tmp, "cm_proceed")
    cm_vim = os.path.join(self.tmp, "cm_vim.py")
    _write_exec(
        cm_vim,
        f"""
        #!/usr/bin/env python3
        import os, sys, time
        open({gate_opened!r}, "w").close()
        while not os.path.exists({gate_proceed!r}):
          time.sleep(0.02)
        with open(sys.argv[-1], "w") as f:
          f.write("after copy mode\\n")
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "before cm", cm_vim, lock_file=lock_file
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(lambda: os.path.exists(gate_opened), desc="cm vim opened")
    self._tmux("copy-mode", "-t", orig_pane)
    self.assertEqual(
        self._tmux("display-message", "-p", "-t", orig_pane, "#{pane_in_mode}")
        .stdout.strip(),
        "1",
    )
    open(gate_proceed, "w", encoding="utf-8").close()
    _wait_until(
        lambda: "CG:2:0:after copy mode" in _read_text(events_log),
        desc="Pass 2 after copy-mode cleared",
    )
    self.assertEqual(_read_text(prompt_file), "after copy mode")

  def test_force_send_bypasses_proc_and_modal_checks(self) -> None:
    conv_id = "99999999-9999-9999-9999-999999999999"
    lock_file = _create_conv_db(self.data_dir, conv_id, 132, 9)
    edit_vim = os.path.join(self.tmp, "force_vim.py")
    _write_exec(
        edit_vim,
        """
        #!/usr/bin/env python3
        import sys
        with open(sys.argv[-1], "w") as f:
          f.write("forced update\\n")
        """,
    )
    orig_pane, prompt_file, events_log = self._start_cli_session(
        "initial", edit_vim, lock_file=lock_file, force_send=True
    )
    self._tmux("send-keys", "-t", orig_pane, "C-g")
    _wait_until(
        lambda: "CG:2:0:forced update" in _read_text(events_log),
        desc="Pass 2 via CODE_HARNESS_TMUX_EDITOR_FORCE_SEND=1",
    )
    self.assertEqual(_read_text(prompt_file), "forced update")
    self.assertEqual(_read_text(self.toast_log), "")


if __name__ == "__main__":
  unittest.main()

"""Unit tests for code_harness.snapshot."""

from __future__ import annotations

import io
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from code_harness import cli, snapshot
from code_harness.config import load_config


def _write_multi_flavor_config(tmp: str) -> pathlib.Path:
  cfg_file = pathlib.Path(tmp) / "config.json"
  cfg_file.write_text(
      json.dumps({
          "default_flavor": "alt",
          "flavors": {
              "alt": {
                  "env_bin": "ALT_CLI_BIN",
                  "candidate_bins": ["/opt/alt/bin/cli", "alt-cli"],
                  "data_dir_name": "alt-cli",
                  "match_basenames": ["alt-cli"],
                  "match_path_substrings": [
                      "/build/cmd/cli/cli",
                      "/opt/alt/bin/cli",
                  ],
                  "match_app_data_basenames": ["alt-cli"],
              },
              "agy": {
                  "extra_match_path_substrings": [
                      "/build/cmd/cli/cli_external",
                  ],
              },
          },
          "extra_non_tui_subcommands": ["custom-headless"],
          "extra_cli_takes_value": ["--custom-opt"],
      }),
      encoding="utf-8",
  )
  return cfg_file


def _encode_varint(value: int) -> bytes:
  out = bytearray()
  while True:
    to_write = value & 0x7F
    value >>= 7
    if value:
      out.append(to_write | 0x80)
    else:
      out.append(to_write)
      return bytes(out)


def _proto_varint_field(field_num: int, value: int) -> bytes:
  return _encode_varint((field_num << 3) | 0) + _encode_varint(value)


def _proto_bytes_field(field_num: int, payload: bytes) -> bytes:
  return (
      _encode_varint((field_num << 3) | 2)
      + _encode_varint(len(payload))
      + payload
  )


class ProcessMatchingTests(unittest.TestCase):

  def test_default_agy_matching_and_ide_exclusion(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      env = {"XDG_CONFIG_HOME": tmp}
      with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
        cfg = load_config(env=env)

    self.assertEqual(
        snapshot.cli_proc_flavor(
            {"argv": ["/home/u/.local/bin/agy"], "exe": ""}, config=cfg, env={}
        ),
        ("agy", 1),
    )
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {"argv": ["python3", "/usr/local/bin/agy", "--model", "x"], "exe": ""},
            config=cfg,
            env={},
        ),
        ("agy", 2),
    )
    # IDE paths must be excluded.
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {
                "argv": ["/Applications/Antigravity.app/Contents/MacOS/agy"],
                "exe": "",
            },
            config=cfg,
            env={},
        ),
        (None, 1),
    )
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {"argv": ["/opt/antigravity/bin/agy"], "exe": ""},
            config=cfg,
            env={},
        ),
        (None, 1),
    )

  def test_multi_flavor_longest_substring_and_app_data_dir(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))

    # Longer substring `/build/cmd/cli/cli_external` (agy) beats
    # `/build/cmd/cli/cli` (alt).
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {
                "argv": ["/build/cmd/cli/cli_external"],
                "exe": "/build/cmd/cli/cli_external",
            },
            config=cfg,
            env={},
        ),
        ("agy", 1),
    )
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {
                "argv": ["/build/cmd/cli/cli"],
                "exe": "/build/cmd/cli/cli",
            },
            config=cfg,
            env={},
        ),
        ("alt", 1),
    )
    # Explicit --app_data_dir selects the matching flavor.
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {
                "argv": [
                    "/build/cmd/cli/cli",
                    "--app_data_dir=antigravity-cli",
                ],
                "exe": "/build/cmd/cli/cli",
            },
            config=cfg,
            env={},
        ),
        ("agy", 1),
    )
    # Flavor env_bin match.
    self.assertEqual(
        snapshot.cli_proc_flavor(
            {"argv": ["/custom/path/my-alt"], "exe": ""},
            config=cfg,
            env={"ALT_CLI_BIN": "/custom/path/my-alt"},
        ),
        ("alt", 1),
    )

  def test_non_tui_subcommands_and_cli_takes_value(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))

    # Extra flag taking a value should not mistake its value for a subcommand.
    self.assertEqual(
        snapshot.is_cli_proc(
            {"argv": ["alt-cli", "--custom-opt", "some-val"], "exe": ""},
            config=cfg,
            env={},
        ),
        (True, False),
    )
    # Extra non-TUI subcommand is excluded.
    self.assertEqual(
        snapshot.is_cli_proc(
            {"argv": ["alt-cli", "custom-headless"], "exe": ""},
            config=cfg,
            env={},
        ),
        (False, False),
    )
    # Built-in non-TUI subcommand & headless flags.
    self.assertEqual(
        snapshot.is_cli_proc(
            {"argv": ["agy", "mcp"], "exe": ""}, config=cfg, env={}
        ),
        (False, False),
    )
    self.assertEqual(
        snapshot.is_cli_proc(
            {"argv": ["agy", "--bg-updater"], "exe": ""}, config=cfg, env={}
        ),
        (False, False),
    )
    self.assertEqual(
        snapshot.is_cli_proc(
            {"argv": ["agy", "-p", "hello"], "exe": ""}, config=cfg, env={}
        ),
        (True, True),
    )

  def test_is_self_script_proc(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"comm": "python3", "argv": ["python3", "/bin/code-harness", "snapshot"]}
        )
    )
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"comm": "code-harness", "argv": ["code-harness", "--builtin", "snapshot", "-n"]}
        )
    )
    self.assertFalse(
        snapshot.is_self_script_proc(
            {"comm": "code-harness", "argv": ["code-harness", "run"]}
        )
    )


class DataDirAndStateTests(unittest.TestCase):

  def test_resolve_cli_data_dir_from_config_flags_and_fds(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      cid = "11111111-2222-3333-4444-555555555555"

      # From FD presence lock path.
      lock_dir = f"{tmp}/custom-data"
      resolved = snapshot.resolve_cli_data_dir(
          {"argv": ["alt-cli"], "exe": ""},
          fds={"3": f"{lock_dir}/presence/{cid}.lock"},
          config=cfg,
          env={},
      )
      self.assertEqual(resolved, lock_dir)

      # From flavor data_dir_name fallback.
      with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", tmp)):
        resolved_alt = snapshot.resolve_cli_data_dir(
            {"argv": ["alt-cli"], "exe": ""}, fds={}, config=cfg, env={}
        )
        resolved_agy = snapshot.resolve_cli_data_dir(
            {"argv": ["agy"], "exe": ""}, fds={}, config=cfg, env={}
        )
      self.assertEqual(resolved_alt, f"{tmp}/.gemini/alt-cli")
      self.assertEqual(resolved_agy, f"{tmp}/.gemini/antigravity-cli")

      # From --gemini_dir + --app_data_dir flags.
      resolved_flags = snapshot.resolve_cli_data_dir(
          {
              "argv": [
                  "agy",
                  "--gemini_dir",
                  f"{tmp}/gem",
                  "--app_data_dir",
                  "my-app",
              ],
              "exe": "",
          },
          fds={},
          config=cfg,
          env={},
      )
      self.assertEqual(resolved_flags, f"{tmp}/gem/my-app")

  def test_sqlite_settled_and_active_task_detection(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
      conv_dir = pathlib.Path(tmp) / "conversations"
      conv_dir.mkdir(parents=True)
      db_path = conv_dir / f"{cid}.db"

      con = sqlite3.connect(db_path)
      con.execute(
          "CREATE TABLE steps "
          "(idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, "
          "task_details BLOB, step_payload BLOB)"
      )
      # Daemon task running after checkpoint should be ignored -> settled.
      daemon_blob = _proto_varint_field(snapshot.TASK_DETAILS_IS_DAEMON, 1)
      con.execute(
          "INSERT INTO steps VALUES (1, ?, ?, NULL, NULL)",
          (snapshot.STEP_TYPE_USER_INPUT, snapshot.STATUS_DONE),
      )
      con.execute(
          "INSERT INTO steps VALUES (2, ?, ?, ?, NULL)",
          (snapshot.STEP_TYPE_GENERIC, snapshot.STATUS_RUNNING, daemon_blob),
      )
      con.commit()
      con.close()

      # Write settled PLANNER_RESPONSE transcript.
      tx_path = (
          pathlib.Path(tmp)
          / "brain"
          / cid
          / ".system_generated"
          / "logs"
          / "transcript.jsonl"
      )
      tx_path.parent.mkdir(parents=True)
      tx_path.write_text(
          json.dumps({
              "type": "PLANNER_RESPONSE",
              "status": "DONE",
              "created_at": "2026-01-01T00:00:00Z",
          })
          + "\n",
          encoding="utf-8",
      )

      settled, reason, _, _ = snapshot.check_db_and_transcript_settled(
          tmp, cid, config=cfg
      )
      self.assertTrue(settled, reason)

      # Non-daemon background task running -> active with task title.
      con = sqlite3.connect(db_path)
      task_blob = _proto_bytes_field(
          snapshot.TASK_DETAILS_TITLE, b"Build project"
      )
      con.execute(
          "INSERT INTO steps VALUES (3, ?, ?, ?, NULL)",
          (snapshot.STEP_TYPE_GENERIC, snapshot.STATUS_RUNNING, task_blob),
      )
      con.commit()
      con.close()

      settled, reason, _, _ = snapshot.check_db_and_transcript_settled(
          tmp, cid, config=cfg
      )
      self.assertFalse(settled)
      self.assertIn("Build project", reason)


class RestoreScriptTests(unittest.TestCase):

  def _sample_sessions(self) -> list[dict]:
    return [{
        "name": "work",
        "attached": True,
        "last_attached": 100,
        "project_env": "demo",
        "window_order": [1],
        "windows": {
            1: {
                "index": 1,
                "id": "@1",
                "active": True,
                "layout": "bb62,80x24,0,0[80x12,0,0,0,80x11,0,13,1]",
                "name": "main",
                "panes": [
                    {
                        "session_name": "work",
                        "window_index": 1,
                        "index": 0,
                        "pane_id": "%0",
                        "pane_pid": 1000,
                        "pane_tty": "/dev/pts/0",
                        "active": True,
                        "cwd": "/tmp/proj-a",
                        "agent": {
                            "pid": 1001,
                            "flavor": "alt",
                            "data_dir": "/tmp/data/alt-cli",
                            "conversation_id": (
                                "11111111-1111-1111-1111-111111111111"
                            ),
                            "settled": True,
                            "reason": "waiting for user input",
                            "label": "First task",
                        },
                        "busy_non_agent": [],
                    },
                    {
                        "session_name": "work",
                        "window_index": 1,
                        "index": 1,
                        "pane_id": "%1",
                        "pane_pid": 2000,
                        "pane_tty": "/dev/pts/1",
                        "active": False,
                        "cwd": "/tmp/proj-b",
                        "agent": {
                            "pid": 2001,
                            "flavor": "agy",
                            "data_dir": "/tmp/data/antigravity-cli",
                            "conversation_id": (
                                "22222222-2222-2222-2222-222222222222"
                            ),
                            "settled": True,
                            "reason": "waiting for user input",
                            "label": "Second task",
                        },
                        "busy_non_agent": [],
                    },
                ],
            }
        },
    }]

  def test_multi_flavor_restore_script_uses_per_flavor_binaries(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      script = snapshot.render_restore_script(
          self._sample_sessions(),
          cfg,
          "2026-11-20T00:00:00Z",
          cli_bins={"alt": "/opt/alt/bin/cli", "agy": "/home/u/.local/bin/agy"},
      )

    self.assertIn("ALT_CLI_BIN=${ALT_CLI_BIN:-/opt/alt/bin/cli}", script)
    self.assertIn("printf -v cli_alt_q '%q' \"$ALT_CLI_BIN\"", script)
    self.assertIn(
        "AGY_CLI_BIN=${AGY_CLI_BIN:-/home/u/.local/bin/agy}", script
    )
    self.assertIn("printf -v cli_agy_q '%q' \"$AGY_CLI_BIN\"", script)
    self.assertIn(
        '" $cli_alt_q --conversation=11111111-1111-1111-1111-111111111111"',
        script,
    )
    self.assertIn(
        '" $cli_agy_q --conversation=22222222-2222-2222-2222-222222222222"',
        script,
    )
    self.assertIn("CODE_HARNESS_TMUX_SOCKET", script)
    self.assertIn("CODE_HARNESS_NO_ATTACH", script)
    self.assertIn("code-harness snapshot --restore", script)
    self.assertIn("PROJECT=demo", script)

  def test_cli_help_lists_snapshot(self) -> None:
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertIn("snapshot", stdout.getvalue())


if __name__ == "__main__":
  unittest.main()

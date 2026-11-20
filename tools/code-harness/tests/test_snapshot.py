"""Unit tests for code_harness.snapshot."""

from __future__ import annotations

import datetime
import io
import json
import os
import pathlib
import sqlite3
import subprocess
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
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"comm": "code-harness", "argv": ["code-harness", "tmux-next", "--permission"]}
        )
    )
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"comm": "code-harness", "argv": ["code-harness", "tmux-editor", "--apply-worker"]}
        )
    )
    self.assertTrue(
        snapshot.is_self_script_proc(
            {
                "comm": "python3",
                "argv": ["python3", "-m", "code_harness.cli", "tmux-editor", "--apply-worker"],
            }
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


OLD_TS = "2026-11-17T01:20:00Z"


def _task_details_blob(is_daemon: bool = False, title: str = "task") -> bytes:
  return _proto_varint_field(
      snapshot.TASK_DETAILS_IS_DAEMON, 1 if is_daemon else 0
  ) + _proto_bytes_field(snapshot.TASK_DETAILS_TITLE, title.encode("utf-8"))


def _step_metadata_blob(epoch_sec: int) -> bytes:
  return _proto_bytes_field(1, _proto_varint_field(1, int(epoch_sec)))


def _generic_file_edit_payload(
    path: str,
    is_artifact: bool = True,
    request_feedback: bool | None = None,
    empty_meta: bool = False,
) -> bytes:
  fe = bytearray()
  fe.extend(_proto_bytes_field(2, path.encode("utf-8")))
  if empty_meta:
    fe.extend(_proto_bytes_field(5, b""))
  elif request_feedback is not None:
    am = (
        _proto_varint_field(6, 1)
        if request_feedback
        else _proto_varint_field(7, 1)
    )
    fe.extend(_proto_bytes_field(5, am))
  if is_artifact:
    fe.extend(_proto_varint_field(6, 1))
  any_msg = _proto_bytes_field(
      1, b"type.googleapis.com/exa.cortex.core.tools.FileEdit"
  ) + _proto_bytes_field(2, bytes(fe))
  gres = _proto_bytes_field(6, any_msg)
  gen = _proto_bytes_field(2, gres)
  return _proto_bytes_field(140, gen)


def _code_action_payload(
    is_artifact: bool = True,
    request_feedback: bool | None = None,
    path: str | None = None,
) -> bytes:
  ca = bytearray()
  if path:
    create_spec = _proto_bytes_field(1, path.encode("utf-8"))
    action_spec = _proto_bytes_field(1, create_spec)
    ca.extend(_proto_bytes_field(1, action_spec))
  if is_artifact:
    ca.extend(_proto_varint_field(21, 1))
  if request_feedback is not None:
    am = _proto_varint_field(6, 1 if request_feedback else 0)
    ca.extend(_proto_bytes_field(23, am))
  return _proto_bytes_field(10, bytes(ca))


def _make_conv(
    data_dir: str,
    conv_id: str,
    steps=None,
    transcript=None,
    with_db: bool = True,
    root_id: str = "",
) -> None:
  """Creates SQLite DB and/or transcript.jsonl fixture for `conv_id`."""
  if with_db:
    conv_dir = os.path.join(data_dir, "conversations")
    os.makedirs(conv_dir, exist_ok=True)
    db_path = os.path.join(conv_dir, f"{conv_id}.db")
    con = sqlite3.connect(db_path)
    try:
      con.execute(
          "CREATE TABLE IF NOT EXISTS steps ("
          "idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, "
          "task_details BLOB, step_payload BLOB, metadata BLOB)"
      )
      con.execute("DELETE FROM steps")
      for row in steps or ((1, 15, 3, None),):
        idx, st_type, status, task_blob = row[:4]
        payload = row[4] if len(row) > 4 else None
        md_blob = row[5] if len(row) > 5 else None
        con.execute(
            "INSERT INTO steps "
            "(idx, step_type, status, task_details, step_payload, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idx, st_type, status, task_blob, payload, md_blob),
        )
      if root_id:
        con.execute(
            "CREATE TABLE IF NOT EXISTS trajectory_metadata_blob (data BLOB)"
        )
        con.execute("DELETE FROM trajectory_metadata_blob")
        meta = _proto_bytes_field(snapshot.META_ROOT, root_id.encode("ascii"))
        con.execute(
            "INSERT INTO trajectory_metadata_blob (data) VALUES (?)",
            (meta,),
        )
      con.commit()
    finally:
      con.close()

  if transcript is not None:
    logs_dir = os.path.join(
        data_dir, "brain", conv_id, ".system_generated", "logs"
    )
    os.makedirs(logs_dir, exist_ok=True)
    tx_path = os.path.join(logs_dir, "transcript.jsonl")
    with open(tx_path, "w", encoding="utf-8") as f:
      for entry in transcript:
        f.write(json.dumps(entry) + "\n")


def _register_subagent(
    data_dir: str, parent_id: str, sub_id: str, role: str = "Swarm Worker"
) -> None:
  sub_dir = os.path.join(
      data_dir, "brain", parent_id, ".system_generated", "subagents"
  )
  os.makedirs(sub_dir, exist_ok=True)
  data = {
      "conversationId": sub_id,
      "subagentDescriptor": {"typeName": "worker", "role": role},
      "state": "SUBAGENT_STATE_ALIVE",
      "spawnStepIndex": 1,
  }
  with open(os.path.join(sub_dir, f"{sub_id}.json"), "w", encoding="utf-8") as f:
    json.dump(data, f)


def _settled_entries(ts: str = OLD_TS) -> list[dict]:
  return [
      {
          "step_index": 1,
          "source": "MODEL",
          "type": "PLANNER_RESPONSE",
          "status": "DONE",
          "created_at": ts,
          "content": "Done.",
      }
  ]


def _planner_with_tools(names: list[str], ts: str = OLD_TS) -> dict:
  return {
      "step_index": 10,
      "source": "MODEL",
      "type": "PLANNER_RESPONSE",
      "status": "DONE",
      "created_at": ts,
      "tool_calls": [{"name": n, "args": {}} for n in names],
  }


def _generic_done(
    content: str, step_index: int = 11, ts: str = OLD_TS
) -> dict:
  return {
      "step_index": step_index,
      "source": "MODEL",
      "type": "GENERIC",
      "status": "DONE",
      "created_at": ts,
      "content": content,
  }


def _sdk_hook_entry(ts: str = OLD_TS) -> dict:
  return {
      "step_index": 99,
      "source": "SYSTEM_SDK",
      "type": "SYSTEM_MESSAGE",
      "status": "DONE",
      "created_at": ts,
      "content": "",
  }


def _with_ts_headers(body: str, ts: str = OLD_TS) -> str:
  return (
      f"Created At: {ts}\nCompleted At: {ts}\n"
      f"Created At: {ts}\nCompleted At: {ts}\n{body}"
  )


class SwarmStateClassificationTests(unittest.TestCase):

  def _classify(self, data_dir: str, conv_id: str) -> tuple[str, str]:
    return snapshot.classify_cli_state(
        {"pid": 999999, "start_epoch": None},
        False,
        "%test-swarm-0",
        conv_id,
        set(),
        {},
        {},
        set(),
        set(),
        data_dir,
    )

  def test_classify_waiting_step_interactions(self) -> None:
    perm_payload = _proto_bytes_field(
        snapshot.STEP_REQUESTED_INTERACTION,
        _proto_bytes_field(snapshot.INTERACTION_PERMISSION, b"1"),
    )
    elic_payload = _proto_bytes_field(
        snapshot.STEP_REQUESTED_INTERACTION,
        _proto_bytes_field(snapshot.INTERACTION_ELICITATION, b"1"),
    )
    ask_payload = _proto_bytes_field(
        snapshot.STEP_REQUESTED_INTERACTION,
        _proto_bytes_field(snapshot.INTERACTION_ASK_QUESTION, b"1"),
    )
    self.assertEqual(
        snapshot.classify_waiting_step(snapshot.STEP_TYPE_GENERIC, perm_payload),
        (snapshot.CLI_STATE_PERMISSION, "waiting on permission check"),
    )
    self.assertEqual(
        snapshot.classify_waiting_step(snapshot.STEP_TYPE_GENERIC, elic_payload),
        (snapshot.CLI_STATE_PERMISSION, "waiting on MCP elicitation"),
    )
    self.assertEqual(
        snapshot.classify_waiting_step(snapshot.STEP_TYPE_GENERIC, ask_payload),
        (snapshot.CLI_STATE_QUESTION, "waiting on user question"),
    )
    self.assertEqual(
        snapshot.classify_waiting_step(snapshot.STEP_TYPE_ASK_QUESTION, None),
        (snapshot.CLI_STATE_QUESTION, "waiting on user question"),
    )

  def test_multi_db_lock_resolves_to_root_trajectory(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    child_id = "22222222-2222-2222-2222-222222222222"
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      _make_conv(tmp, root_id)
      _make_conv(tmp, child_id, root_id=root_id)
      fake_fds = {
          "3": f"{tmp}/presence/{child_id}.lock",
          "4": f"{tmp}/conversations/{root_id}.db",
          "5": f"{tmp}/conversations/{child_id}.db",
      }
      with mock.patch.object(
          snapshot, "list_open_files", return_value=fake_fds
      ):
        cid, open_dbs, cli_dir = snapshot.resolve_cli_conversation_and_dir(
            {"pid": 123, "argv": ["agy"], "exe": ""},
            "%test-multi-db",
            data_dir=tmp,
            config=cfg,
        )
      self.assertEqual(cid, root_id)
      self.assertEqual(open_dbs, {root_id, child_id})
      self.assertEqual(cli_dir, tmp)

  def test_settled_parent_with_parked_swarm_workers_is_settled(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    workers = [
        (
            "22222222-2222-2222-2222-222222222221",
            "Swarm Worker (skeptic)",
            [
                _planner_with_tools(["swarm_wait"]),
                _generic_done(
                    _with_ts_headers(
                        "Waiting for a message that calls on you by name, "
                        "or on @all posted after #9."
                    )
                ),
            ],
        ),
        (
            "22222222-2222-2222-2222-222222222222",
            "Swarm Worker (monitor)",
            [
                _planner_with_tools(["swarm_wait"]),
                _generic_done(
                    _with_ts_headers(
                        "Waiting for any message posted after #10."
                    )
                ),
            ],
        ),
        (
            "22222222-2222-2222-2222-222222222223",
            "Swarm Worker (parser)",
            [
                _planner_with_tools(["post_message", "swarm_wait"]),
                _generic_done('{"status":"posted","seq":7}', step_index=11),
                _generic_done(
                    _with_ts_headers(
                        "Waiting for a message that calls on you by name, "
                        "or on @all posted after #7."
                    ),
                    step_index=12,
                ),
            ],
        ),
        (
            "22222222-2222-2222-2222-222222222224",
            "Swarm Worker (reversed)",
            [
                _planner_with_tools(["swarm_wait", "post_message"]),
                _generic_done(
                    _with_ts_headers(
                        "Waiting for a message that calls on you by name, "
                        "or on @all posted after #8."
                    ),
                    step_index=11,
                ),
                _sdk_hook_entry(),
                _generic_done('{"status":"posted","seq":8}', step_index=12),
                _sdk_hook_entry(),
            ],
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      for wid, role, tx in workers:
        _make_conv(tmp, wid, transcript=tx)
        _register_subagent(tmp, root_id, wid, role=role)
      state, _ = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_SETTLED)

  def test_monitor_with_daemon_task_and_swarm_wait_is_settled(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    mon_id = "22222222-2222-2222-2222-222222222225"
    daemon_blob = _task_details_blob(is_daemon=True, title="Cron: */3 * * * *")
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      _make_conv(
          tmp,
          mon_id,
          steps=[
              (7, 132, 2, daemon_blob),
              (8, 15, 3, None),
              (9, 132, 3, None),
          ],
          transcript=[
              _planner_with_tools(["swarm_wait"]),
              _generic_done(
                  _with_ts_headers(
                      "Waiting for a message that calls on you by name, "
                      "or on @all posted after #5."
                  )
              ),
          ],
      )
      _register_subagent(tmp, root_id, mon_id, role="Swarm Worker (monitor)")
      state, _ = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_SETTLED)

  def test_worker_with_non_daemon_task_and_swarm_wait_is_working(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    wid = "22222222-2222-2222-2222-222222222226"
    task_blob = _task_details_blob(is_daemon=False, title="cargo test")
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      _make_conv(
          tmp,
          wid,
          steps=[
              (7, 132, 2, task_blob),
              (8, 15, 3, None),
              (9, 132, 3, None),
          ],
          transcript=[
              _planner_with_tools(["swarm_wait"]),
              _generic_done(
                  _with_ts_headers(
                      "Waiting for a message that calls on you by name, "
                      "or on @all posted after #5."
                  )
              ),
          ],
      )
      _register_subagent(tmp, root_id, wid, role="Swarm Worker (tests)")
      state, reason = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_WORKING)
      self.assertIn("background task active", reason)

  def test_one_parked_and_one_active_swarm_worker_is_working(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    parked_id = "22222222-2222-2222-2222-222222222227"
    active_id = "22222222-2222-2222-2222-222222222228"
    parked_tx = [
        _planner_with_tools(["swarm_wait"]),
        _generic_done(
            _with_ts_headers("Waiting for any message posted after #5.")
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      _make_conv(tmp, parked_id, transcript=parked_tx)
      _make_conv(
          tmp,
          active_id,
          steps=[(1, 15, 8, None)],
          transcript=_settled_entries(),
      )
      _register_subagent(tmp, root_id, parked_id, role="Swarm Worker (idle)")
      _register_subagent(tmp, root_id, active_id, role="Swarm Worker (gen)")
      state, reason = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_WORKING)
      self.assertIn("generating", reason)

    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      _make_conv(tmp, parked_id, transcript=parked_tx)
      _make_conv(
          tmp,
          active_id,
          transcript=[
              _planner_with_tools(["read_file"]),
              _generic_done("1: hello world\n"),
          ],
      )
      _register_subagent(tmp, root_id, parked_id, role="Swarm Worker (idle)")
      _register_subagent(tmp, root_id, active_id, role="Swarm Worker (tool)")
      state, reason = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_WORKING)
      self.assertIn("turn in progress after generic", reason)

  def test_parked_swarm_worker_with_undelivered_wakeup_is_working(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    wid = "22222222-2222-2222-2222-222222222229"
    msg_id = "33333333-3333-3333-3333-333333333333"
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(tmp, root_id, transcript=_settled_entries())
      _make_conv(
          tmp,
          wid,
          transcript=[
              _planner_with_tools(["swarm_wait"]),
              _generic_done(
                  _with_ts_headers(
                      "Waiting for a message that calls on you by name, "
                      "or on @all posted after #5."
                  )
              ),
          ],
      )
      _register_subagent(tmp, root_id, wid, role="Swarm Worker (skeptic)")
      msg_root = os.path.join(
          tmp, "brain", wid, ".system_generated", "messages"
      )
      undeliv = os.path.join(msg_root, "undelivered")
      os.makedirs(undeliv, exist_ok=True)
      with open(os.path.join(undeliv, msg_id), "w", encoding="utf-8") as f:
        f.write("")
      with open(
          os.path.join(msg_root, f"{msg_id}.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"id": msg_id, "priority": "MESSAGE_PRIORITY_HIGH"}, f)
      state, reason = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_WORKING)
      self.assertIn("wakeup pending", reason)

  def test_blocked_swarm_workers_return_permission_and_question(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    parked_id = "22222222-2222-2222-2222-222222222230"
    blocked_id = "22222222-2222-2222-2222-222222222231"
    parked_tx = [
        _planner_with_tools(["swarm_wait"]),
        _generic_done(
            _with_ts_headers("Waiting for any message posted after #1.")
        ),
    ]
    for step_type, expected in (
        (132, snapshot.CLI_STATE_PERMISSION),
        (138, snapshot.CLI_STATE_QUESTION),
    ):
      with tempfile.TemporaryDirectory() as tmp:
        _make_conv(tmp, root_id, transcript=_settled_entries())
        _make_conv(tmp, parked_id, transcript=parked_tx)
        _make_conv(
            tmp,
            blocked_id,
            steps=[(1, step_type, 9, None)],
            transcript=_settled_entries(),
        )
        _register_subagent(tmp, root_id, parked_id, role="Swarm Worker (idle)")
        _register_subagent(tmp, root_id, blocked_id, role="Swarm Worker (wait)")
        state, _ = self._classify(tmp, root_id)
        self.assertEqual(state, expected)

  def test_root_parent_parked_on_swarm_wait_is_working(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111112"
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(
          tmp,
          root_id,
          transcript=[
              _planner_with_tools(["swarm_wait"]),
              _generic_done(
                  _with_ts_headers(
                      "Waiting for a message that calls on you by name, "
                      "or on @all posted after #5."
                  )
              ),
          ],
      )
      traj = snapshot._inspect_trajectory_state(tmp, root_id)
      self.assertEqual(traj[0], snapshot.CLI_STATE_WORKING)
      self.assertEqual(traj[1], "waiting on swarm board")
      state, reason = self._classify(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_WORKING)
      self.assertEqual(reason, "waiting on swarm board")

  def test_swarm_wait_error_output_is_working(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    wid = "22222222-2222-2222-2222-222222222232"
    for err_text in (
        "Error: invalid since",
        _with_ts_headers("Error: invalid since"),
    ):
      with tempfile.TemporaryDirectory() as tmp:
        _make_conv(tmp, root_id, transcript=_settled_entries())
        _make_conv(
            tmp,
            wid,
            transcript=[
                _planner_with_tools(["swarm_wait"]),
                _generic_done(err_text),
            ],
        )
        _register_subagent(tmp, root_id, wid, role="Swarm Worker (err)")
        state, reason = self._classify(tmp, root_id)
        self.assertEqual(state, snapshot.CLI_STATE_WORKING)
        self.assertIn("turn in progress after generic", reason)

  def test_partial_parallel_batch_without_db_is_working(self) -> None:
    wid = "22222222-2222-2222-2222-222222222233"
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(
          tmp,
          wid,
          with_db=False,
          transcript=[
              _planner_with_tools(["read_file", "swarm_wait"]),
              _generic_done(
                  _with_ts_headers(
                      "Waiting for a message that calls on you by name, "
                      "or on @all posted after #9."
                  )
              ),
          ],
      )
      traj = snapshot._inspect_trajectory_state(tmp, wid, is_child=True)
      self.assertEqual(traj[0], snapshot.CLI_STATE_WORKING)

  def test_chunk_boundary_fallback_without_planner_response(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111111"
    wid = "22222222-2222-2222-2222-222222222234"
    msg = (
        "Waiting for a message that calls on you by name, "
        "or on @all posted after #9."
    )
    for content in (_with_ts_headers(msg), msg):
      with tempfile.TemporaryDirectory() as tmp:
        _make_conv(tmp, root_id, transcript=_settled_entries())
        _make_conv(tmp, wid, with_db=True, transcript=[_generic_done(content)])
        _register_subagent(tmp, root_id, wid, role="Swarm Worker (tail)")
        state, _ = self._classify(tmp, root_id)
        self.assertEqual(state, snapshot.CLI_STATE_SETTLED)
        # Without DB, a lone trailing GENERIC without PLANNER_RESPONSE stays
        # conservative.
        no_db_wid = "22222222-2222-2222-2222-222222222235"
        _make_conv(
            tmp, no_db_wid, with_db=False, transcript=[_generic_done(content)]
        )
        traj = snapshot._inspect_trajectory_state(
            tmp, no_db_wid, is_child=True
        )
        self.assertEqual(traj[0], snapshot.CLI_STATE_WORKING)

  def test_parse_iso_dt_variable_fractional_seconds(self) -> None:
    samples = [
        ("2026-11-17T01:02:03.1Z", 100000),
        ("2026-11-17T01:02:03.1234Z", 123400),
        ("2026-11-17T01:02:03.123456789Z", 123456),
        ("2026-11-17T01:02:03.987654321+00:00", 987654),
    ]
    for raw, expected_us in samples:
      dt = snapshot._parse_iso_dt(raw)
      self.assertIsNotNone(dt)
      assert dt is not None
      self.assertEqual(dt.tzinfo, datetime.timezone.utc)
      self.assertEqual(dt.microsecond, expected_us)

  def test_non_proc_load_process_table_batches_lsof_and_uses_ps_ww(
      self,
  ) -> None:
    ps_stdout = (
        "  101     1 Ss   ttys001  00:10 /bin/zsh\n"
        "  102   101 S+   ttys001  00:05 alt-cli --conversation=1111\n"
        "  103   101 S+   ttys002  00:04 agy\n"
    )
    lsof_stdout = (
        "p102\n"
        "f2\n"
        "n/tmp/alt-cli.log\n"
        "f15\n"
        "n/home/u/.gemini/alt-cli/presence/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1.lock\n"
        "p103\n"
    )

    def fake_run(cmd, **_kwargs):
      if os.path.basename(cmd[0]) == "ps":
        return subprocess.CompletedProcess(cmd, 0, stdout=ps_stdout, stderr="")
      if "lsof" in os.path.basename(cmd[0]):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=lsof_stdout, stderr=""
        )
      raise AssertionError(f"unexpected command: {cmd}")

    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      with (
          mock.patch.object(os.path, "isdir", return_value=False),
          mock.patch.object(
              subprocess, "run", side_effect=fake_run
          ) as mock_run,
      ):
        procs, _ = snapshot.load_process_table(config=cfg)
        self.assertIn(102, procs)
        self.assertIn(103, procs)
        self.assertEqual(mock_run.call_count, 2)
        ps_cmd = mock_run.call_args_list[0][0][0]
        self.assertEqual(ps_cmd[:3], ["ps", "-ww", "-eo"])
        lsof_cmd = mock_run.call_args_list[1][0][0]
        self.assertEqual(
            lsof_cmd[1:],
            ["-n", "-P", "-l", "-a", "-d", "0-999", "-Fn", "-p", "102,103"],
        )

        # Both CLI PIDs (including 103 with 0 regular/numeric FDs) are cached.
        self.assertEqual(
            snapshot.list_open_files(102)["2"], "/tmp/alt-cli.log"
        )
        self.assertEqual(snapshot.list_open_files(103), {})
        self.assertEqual(mock_run.call_count, 2)

      # Graceful {} when lsof binary raises FileNotFoundError.
      with mock.patch.object(
          subprocess, "run", side_effect=FileNotFoundError("no lsof")
      ):
        self.assertEqual(snapshot._lsof_open_files_many([102]), {})

  def test_render_restore_script_quotes_special_session_name(self) -> None:
    bad_name = 's"$bad`id`'
    sessions = [{
        "name": bad_name,
        "attached": True,
        "last_attached": 100,
        "project_env": "",
        "window_order": [0],
        "windows": {
            0: {
                "index": 0,
                "id": "@0",
                "active": True,
                "layout": "",
                "name": "main",
                "panes": [{
                    "session_name": bad_name,
                    "window_index": 0,
                    "index": 0,
                    "pane_id": "%0",
                    "pane_pid": 100,
                    "pane_tty": "/dev/pts/0",
                    "active": True,
                    "cwd": "/tmp",
                    "agent": None,
                    "busy_non_agent": [],
                }],
            },
        },
    }]
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(config_path=_write_multi_flavor_config(tmp))
      script = snapshot.render_restore_script(
          sessions,
          cfg,
          "2026-11-17T00:00:00Z",
          cli_bins={"alt": "/usr/bin/alt-cli"},
      )
    self.assertNotIn(f"session {bad_name} already exists", script)
    self.assertIn(
        "printf 'code-harness snapshot: session %s already exists, "
        "skipping\\n' 's\"$bad`id`' >&2",
        script,
    )

  def test_pane_state_path_honors_xdg_runtime_dir(self) -> None:
    with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/tmp/xdg-rt"}):
      self.assertEqual(
          snapshot._pane_state_path("%42"),
          "/tmp/xdg-rt/code-harness-pane-%42.json",
      )
    with mock.patch.dict(os.environ, {}, clear=True):
      self.assertEqual(
          snapshot._pane_state_path("%42"),
          f"/run/user/{os.getuid()}/code-harness-pane-%42.json",
      )

  def _classify_with_ts(self, data_dir: str, conv_id: str):
    return snapshot.classify_cli_state_with_ts(
        {"pid": 999999, "start_epoch": None},
        False,
        "%test-swarm-0",
        conv_id,
        set(),
        {},
        {},
        set(),
        set(),
        data_dir,
    )

  def _classify_instance(self, data_dir: str, conv_id: str):
    return snapshot.classify_cli_instance(
        {"pid": 999999, "start_epoch": None},
        False,
        "%test-swarm-0",
        conv_id,
        set(),
        {},
        {},
        set(),
        set(),
        data_dir,
    )

  def test_urgent_helpers_and_ui_category(self) -> None:
    self.assertEqual(
        snapshot.CLI_URGENT_STATES,
        frozenset({
            snapshot.CLI_STATE_PERMISSION,
            snapshot.CLI_STATE_QUESTION,
        }),
    )
    self.assertEqual(
        snapshot.CLI_SETTLED_STATES,
        frozenset({
            snapshot.CLI_STATE_SETTLED,
            snapshot.CLI_STATE_PLAN,
        }),
    )
    self.assertTrue(snapshot.is_urgent_cli_state(snapshot.CLI_STATE_PERMISSION))
    self.assertTrue(snapshot.is_urgent_cli_state(snapshot.CLI_STATE_QUESTION))
    self.assertFalse(snapshot.is_urgent_cli_state(snapshot.CLI_STATE_PLAN))
    self.assertFalse(snapshot.is_urgent_cli_state(snapshot.CLI_STATE_SETTLED))
    self.assertFalse(snapshot.is_urgent_cli_state(snapshot.CLI_STATE_WORKING))

    cid = "11111111-1111-1111-1111-111111111111"
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_PERMISSION, "waiting on permission check", cid
        ),
        "perm",
    )
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_QUESTION, "waiting on user question", cid
        ),
        "ask",
    )
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_PLAN, "waiting for plan approval", cid
        ),
        "plan",
    )
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_SETTLED, "waiting for user input", cid
        ),
        "done",
    )
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_SETTLED, "waiting for user input", None
        ),
        "new",
    )
    self.assertEqual(
        snapshot.classify_ui_category(
            snapshot.CLI_STATE_WORKING, "model generating response", cid
        ),
        "work",
    )

  def test_child_permission_overrides_parent_question(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111113"
    child_id = "22222222-2222-2222-2222-222222222236"
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(
          tmp,
          root_id,
          steps=[(1, 138, 9, None, None, _step_metadata_blob(1700000100))],
          transcript=_settled_entries(ts="2026-11-17T01:00:00Z"),
      )
      _make_conv(
          tmp,
          child_id,
          steps=[(1, 132, 9, None, None, _step_metadata_blob(1700000200))],
          transcript=_settled_entries(ts="2026-11-17T01:05:00Z"),
      )
      _register_subagent(tmp, root_id, child_id, role="Runner")
      state, reason, ts = self._classify_with_ts(tmp, root_id)
      self.assertEqual(state, snapshot.CLI_STATE_PERMISSION)
      self.assertIn("Runner", reason)
      self.assertEqual(ts, "2026-11-17T01:05:00Z")

  def test_newest_urgent_child_timestamp_wins_across_children(self) -> None:
    root_id = "11111111-1111-1111-1111-111111111114"
    old_id = "22222222-2222-2222-2222-222222222237"
    new_id = "22222222-2222-2222-2222-222222222238"
    for step_type, expected_state in (
        (132, snapshot.CLI_STATE_PERMISSION),
        (138, snapshot.CLI_STATE_QUESTION),
    ):
      with tempfile.TemporaryDirectory() as tmp:
        _make_conv(tmp, root_id, transcript=_settled_entries())
        _make_conv(
            tmp,
            old_id,
            steps=[(1, step_type, 9, None)],
            transcript=_settled_entries(ts="2026-11-17T01:21:00Z"),
        )
        _make_conv(
            tmp,
            new_id,
            steps=[(1, step_type, 9, None)],
            transcript=_settled_entries(ts="2026-11-17T01:29:00Z"),
        )
        _register_subagent(tmp, root_id, old_id, role="Older")
        _register_subagent(tmp, root_id, new_id, role="Newer")
        state, reason, ts = self._classify_with_ts(tmp, root_id)
        self.assertEqual(state, expected_state)
        self.assertIn("Newer", reason)
        self.assertEqual(ts, "2026-11-17T01:29:00Z")

  def test_plan_approval_generic_code_action_and_sidecar(self) -> None:
    cid = "33333333-3333-3333-3333-333333333301"
    with tempfile.TemporaryDirectory() as tmp:
      plan_dir = os.path.join(tmp, "brain", cid)
      os.makedirs(plan_dir, exist_ok=True)
      plan_path = os.path.join(plan_dir, "implementation_plan.md")
      walk_path = os.path.join(plan_dir, "walkthrough.md")
      task_path = os.path.join(plan_dir, "task.md")
      pl = _generic_file_edit_payload(
          plan_path, is_artifact=True, request_feedback=True
      )
      _make_conv(
          tmp,
          cid,
          steps=[
              (0, 14, 3, None, None, _step_metadata_blob(1700000000)),
              (1, 132, 3, None, pl, _step_metadata_blob(1700000010)),
              (2, 15, 3, None, None, _step_metadata_blob(1700000020)),
          ],
          transcript=_settled_entries(ts="2026-11-17T02:00:00Z"),
      )
      state, reason, ts = self._classify_with_ts(tmp, cid)
      self.assertEqual(state, snapshot.CLI_STATE_PLAN)
      self.assertEqual(reason, "waiting for plan approval")
      self.assertEqual(ts, "2026-11-17T02:00:00Z")
      self.assertEqual(
          self._classify_instance(tmp, cid),
          (True, "waiting for plan approval"),
      )

      # Legacy CODE_ACTION (step_type=5) with requestFeedback=True.
      ca_pl = _code_action_payload(is_artifact=True, request_feedback=True)
      _make_conv(
          tmp,
          cid,
          steps=[
              (0, 14, 3, None),
              (1, 5, 3, None, ca_pl, _step_metadata_blob(1700000030)),
              (2, 15, 3, None),
          ],
          transcript=_settled_entries(ts="2026-11-17T03:00:00Z"),
      )
      state, _, ts = self._classify_with_ts(tmp, cid)
      self.assertEqual(state, snapshot.CLI_STATE_PLAN)
      self.assertEqual(ts, "2026-11-17T03:00:00Z")

      # Same-turn edit setting requestFeedback=False supersedes earlier True.
      pl_false = _generic_file_edit_payload(
          plan_path, is_artifact=True, request_feedback=False
      )
      _make_conv(
          tmp,
          cid,
          steps=[
              (0, 14, 3, None),
              (1, 132, 3, None, pl),
              (2, 132, 3, None, pl_false),
              (3, 15, 3, None),
          ],
          transcript=_settled_entries(),
      )
      self.assertEqual(self._classify(tmp, cid)[0], snapshot.CLI_STATE_SETTLED)

      # walkthrough.md and task.md are never classified as plan.
      for ignored_path in (walk_path, task_path):
        ig_pl = _generic_file_edit_payload(
            ignored_path, is_artifact=True, request_feedback=True
        )
        _make_conv(
            tmp,
            cid,
            steps=[(0, 14, 3, None), (1, 132, 3, None, ig_pl), (2, 15, 3, None)],
            transcript=_settled_entries(),
        )
        self.assertEqual(
            self._classify(tmp, cid)[0], snapshot.CLI_STATE_SETTLED
        )

      # Sidecar .metadata.json fallback when ArtifactMetadata is omitted.
      pl_no_meta = _generic_file_edit_payload(
          plan_path, is_artifact=True, request_feedback=None
      )
      _make_conv(
          tmp,
          cid,
          steps=[(0, 14, 3, None), (1, 132, 3, None, pl_no_meta), (2, 15, 3, None)],
          transcript=_settled_entries(),
      )
      self.assertEqual(self._classify(tmp, cid)[0], snapshot.CLI_STATE_SETTLED)
      with open(f"{plan_path}.metadata.json", "w", encoding="utf-8") as f:
        json.dump({"requestFeedback": True}, f)
      self.assertEqual(self._classify(tmp, cid)[0], snapshot.CLI_STATE_PLAN)

  def test_urgent_waiting_signature_stable_across_transcript_flush(
      self,
  ) -> None:
    cid = "33333333-3333-3333-3333-333333333306"
    with tempfile.TemporaryDirectory() as tmp:
      _make_conv(
          tmp,
          cid,
          steps=[(4, 132, 9, None, None, _step_metadata_blob(1700000100))],
          transcript=_settled_entries(ts="2026-11-17T01:00:00Z"),
      )
      sig1 = snapshot.urgent_waiting_signature(tmp, cid)
      self.assertTrue(sig1.startswith(f"{cid}:4:2023-11-14T"))

      _make_conv(
          tmp,
          cid,
          steps=[(4, 132, 9, None, None, _step_metadata_blob(1700000100))],
          transcript=_settled_entries(ts="2026-11-17T01:05:00Z"),
      )
      sig2 = snapshot.urgent_waiting_signature(tmp, cid)
      self.assertEqual(sig1, sig2)


if __name__ == "__main__":
  unittest.main()

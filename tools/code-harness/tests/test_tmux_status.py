"""Unit tests for code_harness.tmux_status hidden subcommand."""

from __future__ import annotations

import io
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from code_harness import cli, snapshot, tmux_status
from code_harness.config import load_config


def _encode_varint(val: int) -> bytes:
  out = bytearray()
  while True:
    chunk = val & 0x7F
    val >>= 7
    if val:
      out.append(chunk | 0x80)
    else:
      out.append(chunk)
      return bytes(out)


def _encode_proto_field(field_num: int, wire_type: int, value) -> bytes:
  key = _encode_varint((field_num << 3) | wire_type)
  if wire_type == 0:
    return key + _encode_varint(value)
  if wire_type == 2:
    return key + _encode_varint(len(value)) + value
  raise ValueError(f"unsupported wire type {wire_type}")


def _encode_step_metadata(secs: int, nanos: int = 0) -> bytes:
  ts_msg = _encode_proto_field(1, 0, secs)
  if nanos:
    ts_msg += _encode_proto_field(2, 0, nanos)
  return _encode_proto_field(1, 2, ts_msg)


class _FakeTmux:

  def __init__(self, stdout="", returncode=0, client_loc=None):
    self._stdout = stdout
    self._returncode = returncode
    self._client_loc = client_loc

  def run(self, *args, check=True):
    del check
    cmd = args[0] if args else ""
    if cmd == "list-panes":
      return subprocess.CompletedProcess(
          ["tmux", *args], self._returncode, self._stdout, ""
      )
    if cmd == "list-clients":
      if self._client_loc:
        out = f"{self._client_loc[0]}\x1f100\x1f/dev/pts/9\n"
        return subprocess.CompletedProcess(["tmux", *args], 0, out, "")
      return subprocess.CompletedProcess(["tmux", *args], 0, "", "")
    if cmd == "display-message" and self._client_loc:
      out = f"{self._client_loc[0]}\x1f{self._client_loc[1]}\n"
      return subprocess.CompletedProcess(["tmux", *args], 0, out, "")
    return subprocess.CompletedProcess(["tmux", *args], 1, "", "")


class TmuxStatusHiddenCommandTests(unittest.TestCase):

  def _load_default_config(self, tmp: str):
    env = {"XDG_CONFIG_HOME": tmp}
    with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
      return load_config(env=env)

  def test_tmux_status_is_hidden_from_top_level_help(self) -> None:
    self.assertIn("tmux-status", cli.HIDDEN_COMMANDS)
    self.assertNotIn("tmux-status", cli.PUBLIC_COMMANDS)
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertNotIn("tmux-status", stdout.getvalue())

  def test_is_self_script_proc_ignores_tmux_status(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"argv": ["/usr/bin/code-harness", "tmux-status", "--plain"]}
        )
    )
    self.assertFalse(
        snapshot.is_self_script_proc({"argv": ["/usr/bin/agy", "--resume"]})
    )

  def test_list_tmux_windows_parses_bell_and_sorts_panes(self) -> None:
    sep = "\x1f"
    lines = [
        sep.join(["s1", "0", "0", "0", "%0", "100"]),
        sep.join(["s2", "1", "1", "0", "%1", "200"]),
        sep.join(["s3", "0", "0", "1", "%3", "301"]),
        sep.join(["s3", "0", "1", "0", "%2", "300"]),
        "malformed",
    ]
    tmux = _FakeTmux(stdout="\n".join(lines) + "\n")
    wins = tmux_status.list_tmux_windows_and_panes(tmux)
    assert wins is not None
    self.assertEqual(len(wins), 3)
    self.assertEqual(wins[0]["session_name"], "s1")
    self.assertFalse(wins[0]["bell"])
    self.assertEqual(wins[1]["session_name"], "s2")
    self.assertTrue(wins[1]["bell"])
    self.assertEqual(wins[2]["session_name"], "s3")
    self.assertTrue(wins[2]["bell"])
    self.assertEqual([p["index"] for p in wins[2]["panes"]], [0, 1])

    self.assertIsNone(
        tmux_status.list_tmux_windows_and_panes(_FakeTmux(returncode=1))
    )
    self.assertEqual(
        tmux_status.list_tmux_windows_and_panes(_FakeTmux(stdout="")), []
    )
    ep = tmux_status.parse_iso_epoch("2026-11-17T01:02:03.123456789Z")
    self.assertIsNotNone(ep)
    self.assertAlmostEqual(
        ep,
        tmux_status.parse_iso_epoch("2026-11-17T01:02:03.123456Z"),
        places=6,
    )

  def test_format_and_tiebreak_and_escaping(self) -> None:
    wins = [
        {"session_name": "s1", "window_index": "0", "bell": False, "panes": []},
        {"session_name": "s2", "window_index": "0", "bell": True, "panes": []},
        {"session_name": "s3", "window_index": "0", "bell": True, "panes": []},
    ]
    tmux = _FakeTmux(client_loc=("s1", "0"))
    cands = [
        {
            "session_name": "s3",
            "category": "done",
            "win_idx": 2,
            "pane_pos": 0,
            "recency": (1, 10.0, 10.0),
        },
        {
            "session_name": "s2",
            "category": "done",
            "win_idx": 1,
            "pane_pos": 0,
            "recency": (1, 10.0, 10.0),
        },
    ]
    out = tmux_status.format_status_line(
        [], cands, 2, 1, wins, tmux, None, 32, plain=True
    )
    self.assertEqual(out, "s2 ● 2 ◐ 1 ")

    # Permission overrides done/ask, truncates from left, and escapes `#`.
    styled = tmux_status.format_status_line(
        ["proj#one", "proj#two"],
        cands,
        2,
        0,
        wins,
        tmux,
        None,
        max_width=10,
        plain=False,
    )
    self.assertIn("#[fg=colour203,bold]! …roj##two#[nobold] ", styled)
    self.assertIn("#[fg=colour78,bold]● 2#[nobold] ", styled)
    self.assertIn("#[fg=colour75]◐ 0 #[default]", styled)

    # Ask uses ? prefix in gold bold.
    ask_cands = [{
        "session_name": "a#b",
        "category": "ask",
        "win_idx": 0,
        "pane_pos": 0,
        "recency": (1, 20.0, 20.0),
    }]
    ask_styled = tmux_status.format_status_line(
        [], ask_cands, 1, 0, wins, tmux, None, max_width=32, plain=False
    )
    self.assertTrue(
        ask_styled.startswith("#[fg=colour220,bold]? a##b#[nobold] ")
    )

  def test_cmd_filters_done_by_bell_but_keeps_perm_ask_and_counts(self) -> None:
    wins = [
        {
            "session_name": "s_old_bell",
            "window_index": "0",
            "bell": True,
            "panes": [{"index": 0, "pane_id": "%0", "pane_pid": 100}],
        },
        {
            "session_name": "s_new_viewed",
            "window_index": "0",
            "bell": False,
            "panes": [{"index": 0, "pane_id": "%1", "pane_pid": 101}],
        },
        {
            "session_name": "s_new_prompt",
            "window_index": "0",
            "bell": True,
            "panes": [{"index": 0, "pane_id": "%2", "pane_pid": 102}],
        },
    ]

    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      out = io.StringIO()
      with (
          mock.patch.object(
              tmux_status, "TmuxRunner", return_value=_FakeTmux()
          ),
          mock.patch.object(
              tmux_status, "list_tmux_windows_and_panes", return_value=wins
          ),
          mock.patch.object(
              tmux_status, "load_process_table", return_value=({}, {})
          ),
          mock.patch.object(
              tmux_status, "default_data_dir", return_value=tmp
          ),
          mock.patch.object(
              tmux_status, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_status, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_status,
              "find_pane_cli",
              side_effect=[
                  ({"pid": 100}, False),
                  ({"pid": 101}, False),
                  ({"pid": 102}, False),
              ],
          ),
          mock.patch.object(
              tmux_status,
              "resolve_cli_conversation_and_dir",
              side_effect=[
                  ("c1", set(), tmp),
                  ("c2", set(), tmp),
                  (None, set(), tmp),
              ],
          ),
          mock.patch.object(
              tmux_status,
              "classify_cli_state_with_ts",
              side_effect=[
                  ("settled", "waiting for user input", "2026-11-17T01:00:00Z"),
                  ("settled", "waiting for user input", "2026-11-17T02:00:00Z"),
                  ("settled", "empty prompt (no conversation yet)", ""),
              ],
          ),
          mock.patch("sys.stdout", out),
      ):
        rc = tmux_status.cmd_tmux_status(cfg, None, ["--plain"])
      self.assertEqual(rc, 0)
      self.assertEqual(out.getvalue(), "s_old_bell ● 2 ◐ 0 \n")

      # Verify perm and ask show even with bell=False.
      wins_perm_ask = [
          {
              "session_name": "s_ask",
              "window_index": "0",
              "bell": False,
              "panes": [{"index": 0, "pane_id": "%0", "pane_pid": 200}],
          },
          {
              "session_name": "s_perm",
              "window_index": "0",
              "bell": False,
              "panes": [{"index": 0, "pane_id": "%1", "pane_pid": 201}],
          },
      ]
      out_pa = io.StringIO()
      with (
          mock.patch.object(
              tmux_status, "TmuxRunner", return_value=_FakeTmux()
          ),
          mock.patch.object(
              tmux_status,
              "list_tmux_windows_and_panes",
              return_value=wins_perm_ask,
          ),
          mock.patch.object(
              tmux_status, "load_process_table", return_value=({}, {})
          ),
          mock.patch.object(
              tmux_status, "default_data_dir", return_value=tmp
          ),
          mock.patch.object(
              tmux_status, "load_mcp_commands", return_value=set()
          ),
          mock.patch.object(
              tmux_status, "self_ignored_pids", return_value=set()
          ),
          mock.patch.object(
              tmux_status,
              "find_pane_cli",
              side_effect=[
                  ({"pid": 200}, False),
                  ({"pid": 201}, False),
              ],
          ),
          mock.patch.object(
              tmux_status,
              "resolve_cli_conversation_and_dir",
              side_effect=[
                  ("c3", set(), tmp),
                  ("c4", set(), tmp),
              ],
          ),
          mock.patch.object(
              tmux_status,
              "classify_cli_state_with_ts",
              side_effect=[
                  ("question", "waiting on user question", "2026-11-17T03:00:00Z"),
                  (
                      "permission",
                      "waiting on permission check",
                      "2026-11-17T02:00:00Z",
                  ),
              ],
          ),
          mock.patch("sys.stdout", out_pa),
      ):
        rc = tmux_status.cmd_tmux_status(cfg, None, ["--plain"])
      self.assertEqual(rc, 0)
      self.assertEqual(out_pa.getvalue(), "! s_perm ● 1 ◐ 0 \n")

  def test_waiting_step_timestamp_and_classify_with_ts(self) -> None:
    conv_id = "11111111-1111-1111-1111-111111111111"
    with tempfile.TemporaryDirectory() as tmp:
      cfg = self._load_default_config(tmp)
      conv_dir = os.path.join(tmp, "conversations")
      logs_dir = os.path.join(tmp, "brain", conv_id, ".system_generated", "logs")
      os.makedirs(conv_dir)
      os.makedirs(logs_dir)

      # Metadata timestamp is 2026-11-17T02:00:00Z (1794880800),
      # newer than transcript timestamp 2026-11-17T01:00:00Z.
      md_blob = _encode_step_metadata(1794880800)
      con = sqlite3.connect(os.path.join(conv_dir, f"{conv_id}.db"))
      try:
        con.execute(
            "CREATE TABLE steps ("
            "idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, "
            "task_details BLOB, step_payload BLOB, metadata BLOB)"
        )
        con.execute(
            "INSERT INTO steps (idx, step_type, status, metadata) "
            "VALUES (1, ?, ?, ?)",
            (snapshot.STEP_TYPE_ASK_QUESTION, snapshot.STATUS_WAITING, md_blob),
        )
        con.commit()
      finally:
        con.close()

      with open(
          os.path.join(logs_dir, "transcript.jsonl"), "w", encoding="utf-8"
      ) as f:
        f.write(
            json.dumps({
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-11-17T01:00:00Z",
                "content": "asking",
            })
            + "\n"
        )

      state, reason, ts = snapshot.classify_cli_state_with_ts(
          {"pid": 999999},
          False,
          "%0",
          conv_id,
          set(),
          {},
          {},
          set(),
          set(),
          tmp,
          config=cfg,
      )
      self.assertEqual(state, snapshot.CLI_STATE_QUESTION)
      self.assertEqual(reason, "waiting on user question")
      self.assertEqual(ts, "2026-11-17T02:00:00Z")


if __name__ == "__main__":
  unittest.main()

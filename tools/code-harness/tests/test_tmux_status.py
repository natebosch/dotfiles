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

from code_harness import cli, context, snapshot, tmux_status
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
    self.calls: list[tuple[str, ...]] = []

  def run(self, *args, check=True):
    del check
    self.calls.append(args)
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
    if cmd == "set-option":
      return subprocess.CompletedProcess(["tmux", *args], 0, "", "")
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
        [], [], [], 0, cands, 2, 1, wins, tmux, None, 32, plain=True
    )
    self.assertEqual(out, "s2 ◆ 0 ● 2 ◐ 1 ")

    # Permission overrides done, truncates from left, and escapes `#`.
    styled = tmux_status.format_status_line(
        ["proj#one", "proj#two"],
        [],
        [],
        0,
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
    self.assertIn("#[fg=colour206]◆ 0 ", styled)
    self.assertIn("#[fg=colour78,bold]● 2#[nobold] ", styled)
    self.assertIn("#[fg=colour75]◐ 0 #[default]", styled)

    # Ask alone uses ? prefix in gold bold.
    ask_styled = tmux_status.format_status_line(
        [],
        ["a#b"],
        [],
        0,
        [],
        0,
        0,
        wins,
        tmux,
        None,
        max_width=32,
        plain=False,
    )
    self.assertTrue(
        ask_styled.startswith("#[fg=colour220,bold]? a##b#[nobold] ")
    )

  def test_plan_precedence_and_counter_rendering(self) -> None:
    wins = [
        {
            "session_name": "s_plan1",
            "window_index": "0",
            "bell": False,
            "panes": [],
        },
        {
            "session_name": "s_plan2",
            "window_index": "0",
            "bell": False,
            "panes": [],
        },
        {
            "session_name": "s_done",
            "window_index": "0",
            "bell": True,
            "panes": [],
        },
    ]
    tmux = _FakeTmux(client_loc=("s_done", "0"))
    plan_cands = [
        {
            "session_name": "s_plan1",
            "category": "plan",
            "win_idx": 0,
            "pane_pos": 0,
            "recency": (1, 10.0, 10.0),
        },
        {
            "session_name": "s_plan2",
            "category": "plan",
            "win_idx": 1,
            "pane_pos": 0,
            "recency": (1, 20.0, 20.0),
        },
    ]
    done_cands = [
        {
            "session_name": "s_done",
            "category": "done",
            "win_idx": 2,
            "pane_pos": 0,
            "recency": (1, 99.0, 99.0),
        },
    ]

    plain_plan = tmux_status.format_status_line(
        [],
        [],
        plan_cands,
        2,
        done_cands,
        1,
        0,
        wins,
        tmux,
        None,
        32,
        plain=True,
    )
    self.assertEqual(plain_plan, "◆ s_plan2 ◆ 2 ● 1 ◐ 0 ")

    styled_plan = tmux_status.format_status_line(
        [],
        [],
        [plan_cands[0]],
        1,
        done_cands,
        1,
        0,
        wins,
        tmux,
        None,
        32,
        plain=False,
    )
    self.assertEqual(
        styled_plan,
        "#[fg=colour206,bold]◆ s_plan1#[nobold] "
        "#[fg=colour206,bold]◆ 1#[nobold] "
        "#[fg=colour78,bold]● 1#[nobold] "
        "#[fg=colour75]◐ 0 #[default]",
    )

    with_ask = tmux_status.format_status_line(
        [],
        ["s_ask"],
        plan_cands,
        2,
        done_cands,
        1,
        0,
        wins,
        tmux,
        None,
        32,
        plain=True,
    )
    self.assertEqual(with_ask, "? s_ask ◆ 2 ● 1 ◐ 0 ")

    with_perm = tmux_status.format_status_line(
        ["s_perm"],
        [],
        [plan_cands[0]],
        1,
        done_cands,
        1,
        0,
        wins,
        tmux,
        None,
        32,
        plain=True,
    )
    self.assertEqual(with_perm, "! s_perm ◆ 1 ● 1 ◐ 0 ")

  def test_urgent_width_sharing_and_ask_budget(self) -> None:
    fmt = tmux_status.format_urgent_segments
    self.assertEqual(fmt(["p1"], ["a1"], 10, plain=True), "! p1 ? a1 ")
    styled = fmt(["p#1"], ["a#1"], 20, plain=False)
    self.assertEqual(
        styled,
        "#[fg=colour203,bold]! p##1#[nobold] "
        "#[fg=colour220,bold]? a##1#[nobold] ",
    )
    shared = fmt(["x"], ["verylongname"], 12, plain=True)
    self.assertEqual(shared, "! x ? …gname ")
    narrow = fmt(["alpha"], ["beta"], 7, plain=True)
    self.assertEqual(narrow, "! alpha ")
    self.assertNotIn("?", narrow)

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
        rc = tmux_status.cmd_tmux_status(cfg, None, ["--plain", "--no-notify"])
      self.assertEqual(rc, 0)
      self.assertEqual(out.getvalue(), "s_old_bell ◆ 0 ● 2 ◐ 0 \n")

      # Verify perm and ask both surface without bells and ask does not inflate
      # done_count.
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
        rc = tmux_status.cmd_tmux_status(cfg, None, ["--plain", "--no-notify"])
      self.assertEqual(rc, 0)
      self.assertEqual(out_pa.getvalue(), "! s_perm ? s_ask ◆ 0 ● 0 ◐ 0 \n")

  def test_status_notification_dedup_and_env_suppression(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      state_file = os.path.join(tmp, "notify-state.json")
      clients = [{"tty": "/dev/pts/1", "focused": False, "activity": 1}]
      items = [{
          "key": ("%0", "c1", "perm", "2026-11-17T01:00:00Z"),
          "category": "perm",
          "session_name": "proj",
          "reason": "waiting on permission check",
      }]
      with (
          mock.patch.dict(
              os.environ, {"CODE_HARNESS_TMUX_NOTIFY_STATE": state_file}
          ),
          mock.patch.object(
              tmux_status.tmux_notify,
              "send_tmux_notification",
              return_value=1,
          ) as mock_send,
      ):
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: clients
        )
        mock_send.assert_called_once()
        self.assertEqual(
            mock_send.call_args[0][1:],
            ("Agent permission: proj", "waiting on permission check (C-Space f)"),
        )
        self.assertTrue(mock_send.call_args[1]["if_unfocused"])

        # Repeat poll with same item does not resend.
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: clients
        )
        self.assertEqual(mock_send.call_count, 1)

        # Disabled via notify_enabled=False does not send.
        items2 = [{
            "key": ("%0", "c1", "perm", "2026-11-17T01:05:00Z"),
            "category": "perm",
            "session_name": "proj",
            "reason": "waiting on permission check",
        }]
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items2, None, False, lambda: clients
        )
        self.assertEqual(mock_send.call_count, 1)

        # Multiple new items format combined attention toast.
        multi = items2 + [{
            "key": ("%1", "c2", "ask", "2026-11-17T01:06:00Z"),
            "category": "ask",
            "session_name": "other",
            "reason": "waiting on user question",
        }]
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), multi, None, True, lambda: clients
        )
        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(
            mock_send.call_args[0][1:],
            ("Agent attention: proj, other", "2 prompts waiting (C-Space f)"),
        )

  def test_notify_state_path_grace_and_unattached_clients(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      rt_dir = os.path.join(tmp, "rt")
      sock_a = os.path.join(tmp, "sock-a")
      sock_b = os.path.join(tmp, "sock-b")
      open(sock_a, "w", encoding="utf-8").close()
      open(sock_b, "w", encoding="utf-8").close()
      with mock.patch.dict(
          os.environ,
          {"XDG_RUNTIME_DIR": rt_dir, "TMUX": f"{sock_a},100,0"},
          clear=True,
      ):
        path_a = tmux_status.notify_state_path(None)
        path_a_explicit = tmux_status.notify_state_path(sock_a)
      with mock.patch.dict(
          os.environ,
          {"XDG_RUNTIME_DIR": rt_dir, "TMUX": f"{sock_b},101,0"},
          clear=True,
      ):
        path_b = tmux_status.notify_state_path(None)
      self.assertNotEqual(path_a, path_b)
      self.assertEqual(path_a, path_a_explicit)

      state_file = os.path.join(tmp, "grace-state.json")
      clients = [{"tty": "/dev/pts/1", "focused": False, "activity": 1}]
      items = [{
          "key": ("%0", "c1", "perm", "tok1"),
          "category": "perm",
          "session_name": "proj",
          "reason": "waiting on permission check",
      }]
      with (
          mock.patch.dict(
              os.environ, {"CODE_HARNESS_TMUX_NOTIFY_STATE": state_file}
          ),
          mock.patch.object(
              tmux_status.tmux_notify,
              "send_tmux_notification",
              return_value=1,
          ) as mock_send,
      ):
        # 0 attached clients -> do not mark seen until a client attaches.
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: [], now=100.0
        )
        self.assertEqual(mock_send.call_count, 0)
        self.assertFalse(os.path.exists(state_file))

        # Unfocused client attaches -> sends and marks seen.
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: clients, now=101.0
        )
        self.assertEqual(mock_send.call_count, 1)

        # Transient 1-tick drop within 15s grace window -> stays suppressed.
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), [], None, True, lambda: clients, now=105.0
        )
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: clients, now=110.0
        )
        self.assertEqual(mock_send.call_count, 1)

        # Missing > 15s grace window -> expires and re-notifies.
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), [], None, True, lambda: clients, now=130.0
        )
        tmux_status.maybe_send_urgent_notifications(
            _FakeTmux(), items, None, True, lambda: clients, now=131.0
        )
        self.assertEqual(mock_send.call_count, 2)

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

  def test_list_panes_backward_compat_and_reconcile_idempotency(self) -> None:
    sep = "\x1f"
    legacy_line = sep.join(["s1", "0", "0", "0", "%0", "100"])
    legacy_wins = tmux_status.list_tmux_windows_and_panes(
        _FakeTmux(stdout=legacy_line + "\n")
    )
    assert legacy_wins is not None
    self.assertEqual(len(legacy_wins), 1)
    self.assertFalse(legacy_wins[0]["has_ctx_fields"])
    self.assertFalse(legacy_wins[0]["panes"][0]["has_ctx_fields"])

    lines9 = [
        sep.join([
            "s1",
            "0",
            "0",
            "0",
            "%0",
            "100",
            " #[fg=black,bg=colour214,bold] Context: 210K #[default]",
            "",
            "#[fg=colour129][#S]#{@code_harness_context} ",
        ]),
        sep.join([
            "s1",
            "0",
            "0",
            "1",
            "%1",
            "101",
            " #[fg=black,bg=colour214,bold] Context: 210K #[default]",
            "",
            "#[fg=colour129][#S]#{@code_harness_context} ",
        ]),
    ]
    wins9 = tmux_status.list_tmux_windows_and_panes(
        _FakeTmux(stdout="\n".join(lines9) + "\n")
    )
    assert wins9 is not None
    self.assertEqual(len(wins9), 1)
    self.assertTrue(wins9[0]["panes"][0]["has_ctx_fields"])

    limits = {"warn": 150_000, "high": 250_000}
    desired0 = context.format_tmux_context_badge(280_000, limits)
    tmux = _FakeTmux()
    tmux_status.reconcile_tmux_context_options(
        tmux,
        True,
        [
            ("s1", "0", wins9[0]["panes"][0], desired0),
            ("s1", "0", wins9[0]["panes"][1], ""),
        ],
    )
    self.assertEqual(len(tmux.calls), 1)
    joined = " ".join(tmux.calls[0])
    self.assertIn("-g @code_harness_context_pad", joined)
    self.assertIn("=s1:", joined)
    self.assertIn("status-left", joined)
    self.assertIn("@code_harness_auto_status", joined)
    self.assertIn("-p -t %0 @code_harness_context", joined)
    self.assertIn("-qup -t %1 @code_harness_context", joined)
    self.assertIn("-quw -t %0 @code_harness_context", joined)

    # Steady state executes zero set-option calls.
    steady_status_left = (
        "#{?client_prefix,#[reverse],}#[fg=colour129][#S]#[default]"
        "#{?@code_harness_context,#{@code_harness_context},"
        "#{@code_harness_context_pad}}"
    )
    steady_panes = [
        (
            "s1",
            "0",
            {
                "index": 0,
                "pane_id": "%0",
                "pane_pid": 100,
                "context_badge": desired0,
                "context_pad": context.TMUX_CONTEXT_PAD,
                "status_left": steady_status_left,
                "has_ctx_fields": True,
            },
            desired0,
        ),
        (
            "s1",
            "0",
            {
                "index": 1,
                "pane_id": "%1",
                "pane_pid": 101,
                "context_badge": "",
                "context_pad": context.TMUX_CONTEXT_PAD,
                "status_left": steady_status_left,
                "has_ctx_fields": True,
            },
            "",
        ),
    ]
    tmux2 = _FakeTmux()
    tmux_status.reconcile_tmux_context_options(tmux2, True, steady_panes)
    self.assertEqual(tmux2.calls, [])

    # Disabling limits clears global pad and pane badges.
    tmux3 = _FakeTmux()
    tmux_status.reconcile_tmux_context_options(
        tmux3,
        False,
        [
            ("s1", "0", steady_panes[0][2], ""),
            ("s1", "0", steady_panes[1][2], ""),
        ],
    )
    self.assertEqual(len(tmux3.calls), 1)
    joined3 = " ".join(tmux3.calls[0])
    self.assertIn("-gqu @code_harness_context_pad", joined3)
    self.assertIn("-qup -t %0 @code_harness_context", joined3)

  def test_cmd_reconciles_context_badges_for_agent_subagent_print_and_shell(
      self,
  ) -> None:
    sep = "\x1f"
    status_left = (
        "#{?client_prefix,#[reverse],}#[fg=colour129][#S]#[default]"
        "#{?@code_harness_context,#{@code_harness_context},"
        "#{@code_harness_context_pad}}"
    )
    list_out = "\n".join([
        sep.join(["s1", "0", "0", "0", "%0", "100", "", "", status_left]),
        sep.join(["s1", "0", "0", "1", "%1", "101", "", "", status_left]),
        sep.join(["s1", "0", "0", "2", "%2", "102", "stale", "", status_left]),
        sep.join(["s1", "0", "0", "3", "%3", "103", "stale", "", status_left]),
        sep.join(["s1", "0", "0", "4", "%4", "104", "stale", "", status_left]),
    ]) + "\n"

    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({"context_limits": {"warn": 150000, "high": 250000}}),
          encoding="utf-8",
      )
      cfg = load_config(config_path=cfg_file)
      fake_tmux = _FakeTmux(stdout=list_out)
      out = io.StringIO()
      with (
          mock.patch.object(tmux_status, "TmuxRunner", return_value=fake_tmux),
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
                  ({"pid": 100, "argv": ["agy"]}, False),
                  ({"pid": 101, "argv": ["agy"]}, False),
                  ({"pid": 102, "argv": ["agy"]}, False),
                  ({"pid": 103, "argv": ["agy", "-p", "hi"]}, True),
                  (None, False),
              ],
          ),
          mock.patch.object(
              tmux_status,
              "resolve_cli_conversation_and_dir",
              side_effect=[
                  ("11111111-1111-1111-1111-111111111111", set(), tmp),
                  (None, set(), tmp),
                  ("22222222-2222-2222-2222-222222222222", set(), tmp),
                  ("33333333-3333-3333-3333-333333333333", set(), tmp),
              ],
          ),
          mock.patch.object(
              context,
              "inspect_conversation_context",
              side_effect=[
                  {"tokens": 280_000, "is_subagent": False},
                  {"tokens": 280_000, "is_subagent": True},
              ],
          ),
          mock.patch.object(
              tmux_status,
              "classify_cli_state_with_ts",
              side_effect=[
                  ("settled", "waiting for user input", "2026-11-17T01:00:00Z"),
                  ("settled", "empty prompt (no conversation yet)", ""),
                  ("working", "running", "2026-11-17T01:00:00Z"),
                  ("working", "running", "2026-11-17T01:00:00Z"),
              ],
          ),
          mock.patch("sys.stdout", out),
      ):
        rc = tmux_status.cmd_tmux_status(cfg, None, ["--plain", "--no-notify"])
      self.assertEqual(rc, 0)
      set_calls = [c for c in fake_tmux.calls if c and c[0] == "set-option"]
      self.assertEqual(len(set_calls), 1)
      joined = " ".join(set_calls[0])
      self.assertIn("-g @code_harness_context_pad", joined)
      self.assertIn(
          "-p -t %0 @code_harness_context "
          " #[fg=white,bg=colour160,bold] ctx: 280K #[default]",
          joined,
      )
      self.assertIn(
          "-p -t %1 @code_harness_context "
          " #[fg=colour246] ctx: 0    #[default]",
          joined,
      )
      # Subagent (%2), print-mode (%3), and shell (%4) panes are cleared.
      self.assertIn("-qup -t %2 @code_harness_context", joined)
      self.assertIn("-qup -t %3 @code_harness_context", joined)
      self.assertIn("-qup -t %4 @code_harness_context", joined)


if __name__ == "__main__":
  unittest.main()

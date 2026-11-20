"""Unit tests for context_limits config and code_harness.context."""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from code_harness import cli
from code_harness import context as ctx
from code_harness.config import ConfigError, ContextLimits, load_config

STYLE_TAG_RE = re.compile(r"#\[[^\]]*\]")


def _varint(val: int) -> bytes:
  out = bytearray()
  while True:
    b = val & 0x7F
    val >>= 7
    if val:
      out.append(b | 0x80)
    else:
      out.append(b)
      return bytes(out)


def _field_varint(num: int, val: int) -> bytes:
  return _varint((num << 3) | 0) + _varint(val)


def _field_bytes(num: int, payload: bytes) -> bytes:
  return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def _make_conv_db(
    data_dir: pathlib.Path,
    conv_id: str,
    *,
    steps: list[tuple[int, int, bytes]] = (),
    gen_metadata: list[tuple[int, bytes]] = (),
    meta_blob: bytes = b"",
) -> pathlib.Path:
  db_path = data_dir / "conversations" / f"{conv_id}.db"
  db_path.parent.mkdir(parents=True, exist_ok=True)
  if db_path.exists():
    db_path.unlink()
  con = sqlite3.connect(str(db_path))
  try:
    con.execute(
        "CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, "
        "metadata BLOB)"
    )
    con.execute(
        "CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB)"
    )
    con.execute("CREATE TABLE trajectory_metadata_blob (data BLOB)")
    for idx, st, meta in steps:
      con.execute(
          "INSERT INTO steps (idx, step_type, metadata) VALUES (?, ?, ?)",
          (idx, st, meta),
      )
    for idx, blob in gen_metadata:
      con.execute(
          "INSERT INTO gen_metadata (idx, data) VALUES (?, ?)", (idx, blob)
      )
    if meta_blob:
      con.execute(
          "INSERT INTO trajectory_metadata_blob (data) VALUES (?)",
          (meta_blob,),
      )
    con.commit()
  finally:
    con.close()
  return db_path


class ContextLimitsConfigTests(unittest.TestCase):

  def test_default_has_no_context_limits(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
        cfg = load_config(env={"XDG_CONFIG_HOME": tmp})
    self.assertIsNone(cfg.context_limits)
    self.assertIsNone(cfg.flavors["agy"].context_limits)
    self.assertIsNone(cfg.get_context_limits())

  def test_per_flavor_and_top_level_context_limits(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({
              "default_flavor": "alt",
              "context_limits": {"warn": 100000, "high": 200000},
              "flavors": {
                  "alt": {
                      "data_dir_name": "alt-cli",
                      "context_limits": {"warn": 150000, "high": 250000},
                  },
              },
          }),
          encoding="utf-8",
      )
      cfg = load_config(config_path=cfg_file)

    self.assertEqual(
        cfg.get_context_limits(flavor="alt"),
        ContextLimits(warn=150000, high=250000),
    )
    # agy has no flavor-specific override, so it inherits top-level limits.
    self.assertEqual(
        cfg.get_context_limits(flavor="agy"),
        ContextLimits(warn=100000, high=200000),
    )

  def test_invalid_context_limits_raises(self) -> None:
    cases = [
        {"warn": 200000, "high": 100000},
        {"warn": 0, "high": 100000},
        {"warn": True, "high": 100000},
        {"warn": "100", "high": 200},
        "bad",
    ]
    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      for bad in cases:
        cfg_file.write_text(
            json.dumps({"context_limits": bad}), encoding="utf-8"
        )
        with self.assertRaises(ConfigError):
          load_config(config_path=cfg_file)


class ContextInspectionTests(unittest.TestCase):

  def test_format_tokens_and_tiers(self) -> None:
    self.assertEqual(ctx.format_tokens(0), "")
    self.assertEqual(ctx.format_tokens(400), "1K")
    self.assertEqual(ctx.format_tokens(150_400), "150K")
    self.assertEqual(ctx.format_tokens(1_250_000), "1.2M")

    lims = {"warn": 150_000, "high": 250_000}
    self.assertEqual(ctx.tier_of(149_999, lims), 0)
    self.assertEqual(ctx.tier_of(150_000, lims), 1)
    self.assertEqual(ctx.tier_of(250_000, lims), 2)
    self.assertEqual(ctx.tier_of(300_000, None), 0)

  def test_format_tmux_context_badge_tiers_and_widths(self) -> None:
    limits = {"warn": 150_000, "high": 250_000}

    # Preserve format_tokens(0) == "" for overview.
    self.assertEqual(ctx.format_tokens(0), "")
    self.assertEqual(ctx.TMUX_CONTEXT_SLOT_WIDTH, 12)
    self.assertEqual(ctx.TMUX_CONTEXT_PAD, " " * 12)
    self.assertEqual(len(ctx.TMUX_CONTEXT_PAD), 12)

    self.assertEqual(ctx.format_tokens_or_zero(None), "0")
    self.assertEqual(ctx.format_tokens_or_zero(0), "0")
    self.assertEqual(ctx.format_tokens_or_zero(42_000), "42K")
    self.assertEqual(ctx.format_tokens_or_zero(180_000), "180K")
    self.assertEqual(ctx.format_tokens_or_zero(280_000), "280K")
    self.assertEqual(ctx.format_tokens_or_zero(12_000_000), "12M")
    self.assertEqual(ctx.format_tmux_context_badge(280_000, limits=None), "")
    self.assertEqual(
        ctx.format_tmux_context_badge(
            180_000, limits=ContextLimits(warn=150_000, high=250_000)
        ),
        " #[fg=black,bg=colour214,bold] ctx: 180K #[default]",
    )

    cases = [
        (0, "#[fg=colour246]", " ctx: 0    ", False),
        (42_000, "#[fg=colour246]", " ctx: 42K  ", False),
        (180_000, "#[fg=black,bg=colour214,bold]", " ctx: 180K ", True),
        (280_000, "#[fg=white,bg=colour160,bold]", " ctx: 280K ", True),
        (12_000_000, "#[fg=white,bg=colour160,bold]", " ctx: 12M  ", True),
    ]
    for tok, style_prefix, vis_tail, has_bg in cases:
      badge = ctx.format_tmux_context_badge(tok, limits=limits)
      self.assertIn(style_prefix, badge)
      if not has_bg:
        self.assertNotIn("bg=", badge)
      vis = STYLE_TAG_RE.sub("", badge)
      self.assertEqual(len(vis), 12)
      self.assertEqual(vis, " " + vis_tail)

  def test_inspect_from_planner_usage_and_gen_metadata_fallback(self) -> None:
    cid1 = "11111111-1111-1111-1111-111111111111"
    cid2 = "22222222-2222-2222-2222-222222222222"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      usage_blob = (
          _field_varint(ctx.USAGE_INPUT, 10_000)
          + _field_varint(ctx.USAGE_CACHE_READ, 140_000)
          + _field_varint(ctx.USAGE_CACHE_WRITE, 20_000)
      )
      step_meta = _field_bytes(ctx.STEP_MODEL_USAGE, usage_blob)
      _make_conv_db(
          data_dir,
          cid1,
          steps=[(0, ctx.PLANNER_RESPONSE, step_meta)],
      )

      # Second DB has no PLANNER_RESPONSE usage, falls back to gen_metadata.
      window = _field_varint(ctx.ESTIMATED, 260_000)
      chat_start = _field_bytes(ctx.CONTEXT_WINDOW, window)
      chat_model = _field_bytes(ctx.CHAT_START, chat_start)
      gen_blob = _field_bytes(ctx.GEN_CHAT_MODEL, chat_model)
      parent_meta = _field_bytes(ctx.META_PARENT, cid1.encode("ascii"))
      _make_conv_db(
          data_dir,
          cid2,
          gen_metadata=[(0, gen_blob)],
          meta_blob=parent_meta,
      )

      lims = {"warn": 150_000, "high": 250_000}
      res1 = ctx.inspect_conversation_context(
          cid1, data_dir=str(data_dir), limits=lims
      )
      self.assertTrue(res1["enabled"])
      self.assertTrue(res1["has_db"])
      self.assertFalse(res1["is_subagent"])
      self.assertEqual(res1["tokens"], 170_000)
      self.assertEqual(res1["formatted"], "170K")
      self.assertEqual(res1["severity"], ctx.SEVERITY_WARN)
      self.assertEqual(res1["tier"], 1)

      res2 = ctx.inspect_conversation_context(
          cid2, data_dir=str(data_dir), limits=lims
      )
      self.assertTrue(res2["is_subagent"])
      self.assertEqual(res2["tokens"], 260_000)
      self.assertEqual(res2["severity"], ctx.SEVERITY_HIGH)
      self.assertEqual(res2["tier"], 2)

      # When limits are None, warning tiers are disabled.
      res_disabled = ctx.inspect_conversation_context(
          cid2, data_dir=str(data_dir), limits=None
      )
      self.assertFalse(res_disabled["enabled"])
      self.assertEqual(res_disabled["severity"], ctx.SEVERITY_DISABLED)
      self.assertEqual(res_disabled["tokens"], 260_000)

  def test_turn_ended(self) -> None:
    cid = "33333333-3333-3333-3333-333333333333"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      tool_meta = (
          _field_varint(ctx.STEP_SOURCE, ctx.SOURCE_MODEL)
          + _field_bytes(ctx.STEP_TOOL_CALL, b"run")
      )
      _make_conv_db(
          data_dir,
          cid,
          steps=[
              (0, ctx.PLANNER_RESPONSE, b""),
              (1, 99, tool_meta),
          ],
      )
      con = ctx.open_conv_db(cid, data_dir=str(data_dir))
      self.assertIsNotNone(con)
      try:
        self.assertFalse(ctx.turn_ended(con, 0))
      finally:
        con.close()

      _make_conv_db(
          data_dir,
          cid,
          steps=[
              (0, 99, tool_meta),
              (1, ctx.PLANNER_RESPONSE, b""),
          ],
      )
      con = ctx.open_conv_db(cid, data_dir=str(data_dir))
      self.assertIsNotNone(con)
      try:
        self.assertTrue(ctx.turn_ended(con, 0))
      finally:
        con.close()

  def test_cli_hidden_context_command_and_limits_file(self) -> None:
    cid = "44444444-4444-4444-4444-444444444444"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      usage_blob = _field_varint(ctx.USAGE_INPUT, 180_000)
      _make_conv_db(
          data_dir,
          cid,
          steps=[(0, ctx.PLANNER_RESPONSE, _field_bytes(ctx.STEP_MODEL_USAGE, usage_blob))],
      )
      limits_file = pathlib.Path(tmp) / "limits.json"
      limits_file.write_text(
          json.dumps({"warn": 150000, "high": 250000}), encoding="utf-8"
      )

      stdout = io.StringIO()
      env = {
          "XDG_CONFIG_HOME": tmp,
          "CODE_HARNESS_CONTEXT_LIMITS_FILE": str(limits_file),
          "CODE_HARNESS_NO_PLUGINS": "1",
      }
      with (
          mock.patch.dict(os.environ, env, clear=True),
          mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)),
          mock.patch("sys.stdout", stdout),
      ):
        rc = cli.main(["context", "--data-dir", str(data_dir), cid])
      self.assertEqual(rc, 0)
      payload = json.loads(stdout.getvalue())
      self.assertTrue(payload["enabled"])
      self.assertEqual(payload["severity"], "warn")
      self.assertEqual(payload["tokens"], 180_000)

    # Hidden subcommand should not appear in top-level --help.
    help_out = io.StringIO()
    with mock.patch("sys.stdout", help_out):
      cli.main(["--help"])
    self.assertNotIn("  context", help_out.getvalue())


def _usage_blob(
    inp: int,
    cache_read: int = 0,
    cache_write: int = 0,
    epoch_sec: int | None = None,
) -> bytes:
  usage = bytearray()
  if inp:
    usage.extend(_field_varint(2, inp))
  if cache_write:
    usage.extend(_field_varint(4, cache_write))
  if cache_read:
    usage.extend(_field_varint(5, cache_read))
  out = bytearray(_field_bytes(9, bytes(usage)))
  if epoch_sec is not None:
    out.extend(_field_bytes(1, _field_varint(1, int(epoch_sec))))
  return bytes(out)


def _step_meta_ts(epoch_sec: int) -> bytes:
  return _field_bytes(1, _field_varint(1, int(epoch_sec)))


def _traj_meta_blob(
    parent: str = "", root: str = "", type_name: str = "", role: str = ""
) -> bytes:
  out = bytearray()
  if parent:
    out.extend(_field_bytes(5, parent.encode("utf-8")))
  if root:
    out.extend(_field_bytes(6, root.encode("utf-8")))
  if type_name or role:
    spec = bytearray()
    if type_name:
      spec.extend(_field_bytes(1, type_name.encode("utf-8")))
    if role:
      spec.extend(_field_bytes(2, role.encode("utf-8")))
    out.extend(_field_bytes(8, bytes(spec)))
  return bytes(out)


def _workflow_step_payload(*agent_ids: str) -> bytes:
  blocks = bytearray()
  for cid in agent_ids:
    blocks.extend(
        _field_bytes(10, _field_bytes(4, _field_bytes(1, cid.encode("utf-8"))))
    )
  render_info = _field_bytes(3, bytes(blocks))
  return _field_bytes(140, _field_bytes(2, render_info))


def _daemon_task_blob() -> bytes:
  return _field_varint(6, 1)


def _create_child_conv_db(
    data_dir: pathlib.Path,
    conv_id: str,
    steps: tuple = (),
    parent: str = "",
    root: str = "",
    type_name: str = "",
    role: str = "",
    minimal: bool = False,
) -> pathlib.Path:
  conv_dir = data_dir / "conversations"
  conv_dir.mkdir(parents=True, exist_ok=True)
  db_path = conv_dir / f"{conv_id}.db"
  if db_path.exists():
    db_path.unlink()
  con = sqlite3.connect(str(db_path))
  try:
    if minimal:
      con.execute(
          "CREATE TABLE steps ("
          "idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER)"
      )
    else:
      con.execute(
          "CREATE TABLE steps ("
          "idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, "
          "task_details BLOB, step_payload BLOB, metadata BLOB)"
      )
    con.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
    con.execute("CREATE TABLE trajectory_metadata_blob (data BLOB)")
    for row in steps:
      idx, st_type, st_val = row[:3]
      if minimal:
        con.execute(
            "INSERT INTO steps (idx, step_type, status) VALUES (?, ?, ?)",
            (idx, st_type, st_val),
        )
      else:
        td_blob = row[3] if len(row) > 3 else None
        pl_blob = row[4] if len(row) > 4 else None
        md_blob = row[5] if len(row) > 5 else None
        con.execute(
            "INSERT INTO steps "
            "(idx, step_type, status, task_details, step_payload, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idx, st_type, st_val, td_blob, pl_blob, md_blob),
        )
    meta_blob = _traj_meta_blob(
        parent=parent, root=root, type_name=type_name, role=role
    )
    if meta_blob:
      con.execute(
          "INSERT INTO trajectory_metadata_blob (data) VALUES (?)",
          (meta_blob,),
      )
    con.commit()
  finally:
    con.close()
  return db_path


def _register_subagent_json(
    data_dir: pathlib.Path,
    parent_id: str,
    child_id: str,
    type_name: str = "worker",
    role: str = "Worker",
    state: str = "SUBAGENT_STATE_ALIVE",
    spawn_step_index: int = 1,
) -> pathlib.Path:
  sub_dir = data_dir / "brain" / parent_id / ".system_generated" / "subagents"
  sub_dir.mkdir(parents=True, exist_ok=True)
  path = sub_dir / f"{child_id}.json"
  path.write_text(
      json.dumps({
          "conversationId": child_id,
          "subagentDescriptor": {"typeName": type_name, "role": role},
          "state": state,
          "spawnStepIndex": spawn_step_index,
      }),
      encoding="utf-8",
  )
  return path


def _write_swarm_roster(
    data_dir: pathlib.Path, root_id: str, members: dict
) -> pathlib.Path:
  swarm_dir = data_dir / "brain" / root_id / ".system_generated" / "swarm"
  swarm_dir.mkdir(parents=True, exist_ok=True)
  path = swarm_dir / "members.json"
  path.write_text(json.dumps({"members": members}), encoding="utf-8")
  return path


class ContextChildTreeTests(unittest.TestCase):

  DEFAULT_LIMITS = {"warn": 250_000, "high": 400_000}

  def test_compaction_count_and_resolve_root(self) -> None:
    root = "10000000-0000-0000-0000-000000000001"
    mid = "10000000-0000-0000-0000-000000000002"
    leaf = "10000000-0000-0000-0000-000000000003"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=(
              (1, 23, 3),
              (2, 23, 2),  # not DONE -> ignored
              (3, 15, 3, None, None, _usage_blob(50_000)),
              (4, 23, 3),
          ),
      )
      _create_child_conv_db(data_dir, mid, parent=root)
      _create_child_conv_db(data_dir, leaf, parent=mid)

      con = ctx.open_conv_db(leaf, data_dir=str(data_dir))
      self.assertIsNotNone(con)
      assert con is not None
      try:
        self.assertEqual(
            ctx.resolve_root_conversation_id(
                leaf, data_dir=str(data_dir), con=con
            ),
            root,
        )
        # Caller's connection must remain open.
        self.assertEqual(con.execute("SELECT 1").fetchone()[0], 1)
      finally:
        con.close()

      r_con = ctx.open_conv_db(root, data_dir=str(data_dir))
      assert r_con is not None
      try:
        self.assertEqual(ctx.compaction_count(r_con), 2)
      finally:
        r_con.close()

      self.assertEqual(
          ctx.resolve_root_conversation_id(
              "not-a-uuid", data_dir=str(data_dir)
          ),
          "",
      )

      # Cycle guard terminates cleanly.
      c1 = "10000000-0000-0000-0000-000000000011"
      c2 = "10000000-0000-0000-0000-000000000012"
      _create_child_conv_db(data_dir, c1, parent=c2)
      _create_child_conv_db(data_dir, c2, parent=c1)
      self.assertEqual(
          ctx.resolve_root_conversation_id(c1, data_dir=str(data_dir)),
          c2,
      )

  def test_warm_killed_and_superseded_workers(self) -> None:
    root = "20000000-0000-0000-0000-000000000001"
    w1 = "20000000-0000-0000-0000-000000000002"
    w2 = "20000000-0000-0000-0000-000000000003"
    wk = "20000000-0000-0000-0000-000000000004"

    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=((1, 15, 3, None, None, _usage_blob(120_000)),),
      )
      _create_child_conv_db(
          data_dir,
          w1,
          parent=root,
          root=root,
          type_name="worker",
          steps=(
              (1, 23, 3),
              (2, 15, 3, None, None, _usage_blob(350_000, epoch_sec=1_000)),
          ),
      )
      _create_child_conv_db(
          data_dir,
          w2,
          parent=root,
          root=root,
          type_name="worker",
          steps=((1, 15, 3, None, None, _usage_blob(30_000, epoch_sec=2_000)),),
      )
      _create_child_conv_db(
          data_dir,
          wk,
          parent=root,
          root=root,
          type_name="worker",
          steps=(
              (1, 15, 3, None, None, _usage_blob(400_000, epoch_sec=9_000)),
          ),
      )

      _register_subagent_json(
          data_dir, root, w1, type_name="worker", role="Impl", spawn_step_index=5
      )
      _register_subagent_json(
          data_dir,
          root,
          wk,
          type_name="worker",
          role="Killed",
          state="SUBAGENT_STATE_KILLED",
          spawn_step_index=2,
      )
      info = ctx.inspect_conversation_context(
          root,
          data_dir=str(data_dir),
          limits=self.DEFAULT_LIMITS,
          extra_child_ids=(wk,),
      )
      self.assertEqual(info["self_tokens"], 120_000)
      self.assertEqual(info["child_tokens"], 350_000)
      self.assertEqual(info["tokens"], 470_000)
      self.assertEqual(info["tier"], 2)
      self.assertEqual(info["severity"], "high")
      self.assertEqual(info["child_count"], 1)
      self.assertEqual(info["compactions"], 1)
      self.assertEqual(info["self_compactions"], 0)
      self.assertEqual(info["child_compactions"], 1)
      self.assertFalse(info["children"][0]["running"])

      # Newer worker w2 supersedes older w1 even while w1 stays ALIVE.
      _register_subagent_json(
          data_dir,
          root,
          w2,
          type_name="worker",
          role="Impl2",
          spawn_step_index=40,
      )
      info2 = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info2["tokens"], 150_000)
      self.assertEqual(info2["child_count"], 1)
      self.assertEqual(info2["children"][0]["id"], w2)
      self.assertEqual(info2["compactions"], 0)

      # Reusing w1 updates its latest step epoch -> w1 wins again.
      _create_child_conv_db(
          data_dir,
          w1,
          parent=root,
          root=root,
          type_name="worker",
          steps=(
              (1, 23, 3),
              (2, 15, 3, None, None, _usage_blob(360_000, epoch_sec=5_000)),
          ),
      )
      info3 = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info3["tokens"], 480_000)
      self.assertEqual(info3["children"][0]["id"], w1)

  def test_oneshot_subagent_running_vs_settled_and_daemon(self) -> None:
    root = "30000000-0000-0000-0000-000000000001"
    sub = "30000000-0000-0000-0000-000000000002"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=((1, 15, 3, None, None, _usage_blob(100_000)),),
      )
      _create_child_conv_db(
          data_dir,
          sub,
          parent=root,
          root=root,
          type_name="researcher",
          steps=(
              (1, 15, 3, None, None, _usage_blob(180_000)),
              (2, 132, 2),  # STATUS_RUNNING
          ),
      )
      _register_subagent_json(
          data_dir, root, sub, type_name="researcher", role="Searcher"
      )
      info_run = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info_run["tokens"], 280_000)
      self.assertEqual(info_run["tier"], 1)
      self.assertTrue(info_run["children"][0]["running"])

      # Settled PLANNER_RESPONSE -> excluded unless active_child_ids overrides.
      _create_child_conv_db(
          data_dir,
          sub,
          parent=root,
          root=root,
          type_name="researcher",
          steps=((1, 15, 3, None, None, _usage_blob(180_000)),),
      )
      info_done = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info_done["tokens"], 100_000)
      self.assertEqual(info_done["child_count"], 0)

      info_hint = ctx.inspect_conversation_context(
          root,
          data_dir=str(data_dir),
          limits=self.DEFAULT_LIMITS,
          active_child_ids=(sub,),
      )
      self.assertEqual(info_hint["tokens"], 280_000)
      self.assertEqual(info_hint["child_count"], 1)

      # Daemon task alone does not mark settled subagent running.
      _create_child_conv_db(
          data_dir,
          sub,
          parent=root,
          root=root,
          type_name="researcher",
          steps=(
              (1, 23, 3),
              (2, 132, 2, _daemon_task_blob()),
              (3, 15, 3, None, None, _usage_blob(180_000)),
          ),
      )
      info_daemon = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info_daemon["tokens"], 100_000)

      # Recent (<60s) USER_INPUT (with trailing housekeeping) -> running.
      _create_child_conv_db(
          data_dir,
          sub,
          parent=root,
          root=root,
          type_name="researcher",
          steps=(
              (1, 15, 3, None, None, _usage_blob(180_000)),
              (2, 14, 3, None, None, _step_meta_ts(int(time.time()) - 5)),
              (3, 101, 3),
          ),
      )
      self.assertEqual(
          ctx.inspect_conversation_context(
              root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
          )["tokens"],
          280_000,
      )

      # Older (>60s) USER_INPUT -> settled.
      _create_child_conv_db(
          data_dir,
          sub,
          parent=root,
          root=root,
          type_name="researcher",
          steps=(
              (1, 15, 3, None, None, _usage_blob(180_000)),
              (2, 14, 3, None, None, _step_meta_ts(int(time.time()) - 120)),
          ),
      )
      self.assertEqual(
          ctx.inspect_conversation_context(
              root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
          )["tokens"],
          100_000,
      )

  def test_swarm_roster_includes_waiting_and_working_and_drops_killed(
      self,
  ) -> None:
    root = "40000000-0000-0000-0000-000000000001"
    s1 = "40000000-0000-0000-0000-000000000002"
    s2 = "40000000-0000-0000-0000-000000000003"
    s3 = "40000000-0000-0000-0000-000000000004"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=((1, 15, 3, None, None, _usage_blob(110_000)),),
      )
      _create_child_conv_db(
          data_dir,
          s1,
          parent=root,
          root=root,
          type_name="worker",
          steps=((1, 15, 3, None, None, _usage_blob(140_000)),),
      )
      _create_child_conv_db(
          data_dir,
          s2,
          parent=root,
          root=root,
          type_name="worker",
          steps=(
              (1, 15, 3, None, None, _usage_blob(160_000)),
              (2, 132, 2),
          ),
      )
      _create_child_conv_db(
          data_dir,
          s3,
          parent=root,
          root=root,
          type_name="worker",
          steps=((1, 15, 3, None, None, _usage_blob(250_000)),),
      )
      _write_swarm_roster(
          data_dir,
          root,
          {
              "coordinator": {
                  "conversation_id": root,
                  "role": "coordinator",
                  "phase": "WAITING",
              },
              "alpha": {
                  "conversation_id": s1,
                  "role": "reviewer-a",
                  "phase": "WAITING",
              },
              "beta": {
                  "conversation_id": s2,
                  "role": "reviewer-b",
                  "phase": "WORKING",
                  "desired_state": "WORKING",
              },
              "gamma": {
                  "conversation_id": s3,
                  "role": "reviewer-c",
                  "phase": "KILLED",
              },
          },
      )
      _register_subagent_json(
          data_dir, root, s1, type_name="worker", role="Swarm a"
      )
      _register_subagent_json(
          data_dir, root, s2, type_name="worker", role="Swarm b"
      )
      _register_subagent_json(
          data_dir, root, s3, type_name="worker", role="Swarm c"
      )

      info = ctx.inspect_conversation_context(
          root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertEqual(info["tokens"], 410_000)
      self.assertEqual(info["child_count"], 2)
      self.assertEqual({c["id"] for c in info["children"]}, {s1, s2})
      self.assertTrue(all(c["type"] == "swarm" for c in info["children"]))

  def test_workflow_agents_and_unrelated_open_db_filtering(self) -> None:
    root = "50000000-0000-0000-0000-000000000001"
    wf1 = "50000000-0000-0000-0000-000000000002"
    wf2 = "50000000-0000-0000-0000-000000000003"
    unrelated = "50000000-0000-0000-0000-000000000099"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=(
              (1, 15, 3, None, None, _usage_blob(90_000)),
              (2, 132, 2, None, _workflow_step_payload(wf1)),
          ),
      )
      _create_child_conv_db(
          data_dir,
          wf1,
          parent="",
          root=root,
          steps=((1, 15, 3, None, None, _usage_blob(170_000)),),
      )
      _create_child_conv_db(
          data_dir,
          wf2,
          parent="",
          root=root,
          steps=((1, 15, 3, None, None, _usage_blob(50_000)),),
      )
      _create_child_conv_db(
          data_dir,
          unrelated,
          parent="",
          root=unrelated,
          steps=((1, 15, 3, None, None, _usage_blob(999_000)),),
      )

      info = ctx.inspect_conversation_context(
          root,
          data_dir=str(data_dir),
          limits=self.DEFAULT_LIMITS,
          extra_child_ids=(unrelated,),
          active_child_ids=(wf2,),
      )
      self.assertEqual(info["tokens"], 310_000)
      self.assertEqual(info["child_count"], 2)
      self.assertEqual({c["id"] for c in info["children"]}, {wf1, wf2})
      self.assertTrue(all(c["type"] == "workflow" for c in info["children"]))

      # Parent workflow step finishes -> wf1 settled and excluded.
      _create_child_conv_db(
          data_dir,
          root,
          steps=(
              (1, 15, 3, None, None, _usage_blob(90_000)),
              (2, 132, 3, None, _workflow_step_payload(wf1)),
          ),
      )
      info_done = ctx.inspect_conversation_context(
          root,
          data_dir=str(data_dir),
          limits=self.DEFAULT_LIMITS,
          extra_child_ids=(wf1, unrelated),
      )
      self.assertEqual(info_done["tokens"], 90_000)
      self.assertEqual(info_done["child_count"], 0)

      # Minimal steps schema works cleanly.
      minimal_root = "50000000-0000-0000-0000-000000000010"
      _create_child_conv_db(
          data_dir, minimal_root, steps=((1, 15, 3),), minimal=True
      )
      info_min = ctx.inspect_conversation_context(
          minimal_root, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertTrue(info_min["has_db"])
      self.assertEqual(info_min["tokens"], 0)

  def test_nested_grandchild_bfs_and_cross_level_worker_isolation(
      self,
  ) -> None:
    root = "60000000-0000-0000-0000-000000000001"
    w = "60000000-0000-0000-0000-000000000002"
    gc_sub = "60000000-0000-0000-0000-000000000003"
    gc_w = "60000000-0000-0000-0000-000000000004"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp) / "data"
      _create_child_conv_db(
          data_dir,
          root,
          steps=((1, 15, 3, None, None, _usage_blob(100_000)),),
      )
      _create_child_conv_db(
          data_dir,
          w,
          parent=root,
          root=root,
          type_name="worker",
          steps=(
              (1, 23, 3),
              (2, 15, 3, None, None, _usage_blob(180_000)),
          ),
      )
      _create_child_conv_db(
          data_dir,
          gc_sub,
          parent=w,
          root=root,
          type_name="researcher",
          steps=(
              (1, 15, 3, None, None, _usage_blob(70_000)),
              (2, 132, 2),
          ),
      )
      _create_child_conv_db(
          data_dir,
          gc_w,
          parent=w,
          root=root,
          type_name="worker",
          steps=((1, 15, 3, None, None, _usage_blob(60_000)),),
      )
      _register_subagent_json(
          data_dir, root, w, type_name="worker", role="LeadW"
      )
      _register_subagent_json(
          data_dir, w, gc_sub, type_name="researcher", role="Helper"
      )
      _register_subagent_json(data_dir, w, gc_w, type_name="worker", role="SubW")

      info = ctx.inspect_conversation_context(
          root,
          data_dir=str(data_dir),
          limits=self.DEFAULT_LIMITS,
          extra_child_ids=(w, gc_w),
      )
      self.assertEqual(info["tokens"], 410_000)
      self.assertEqual(info["child_count"], 3)
      self.assertEqual(info["compactions"], 1)
      self.assertEqual({c["id"] for c in info["children"]}, {w, gc_sub, gc_w})

      # Inspecting child directly sets is_subagent=True and resolves root_id.
      sub_info = ctx.inspect_conversation_context(
          gc_w, data_dir=str(data_dir), limits=self.DEFAULT_LIMITS
      )
      self.assertTrue(sub_info["is_subagent"])
      self.assertEqual(sub_info["root_id"], root)
      self.assertEqual(sub_info["tokens"], 60_000)


if __name__ == "__main__":
  unittest.main()

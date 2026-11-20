"""Unit tests for context_limits config and code_harness.context."""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sqlite3
import tempfile
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


if __name__ == "__main__":
  unittest.main()

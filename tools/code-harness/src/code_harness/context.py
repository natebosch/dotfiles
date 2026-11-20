"""Conversation context-size inspection and per-flavor limit evaluation.

Reads prompt token usage from coding-agent conversation SQLite databases and
evaluates warning tiers against `context_limits` configured on a flavor (or at
top level) in `code-harness` config.json, or an optional override file pointed
to by `CODE_HARNESS_CONTEXT_LIMITS_FILE`.

When no context limits are configured, `load_context_limits()` returns `None`
and warning badges/nudges are suppressed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
from typing import Any, Mapping

from code_harness.config import (
    ContextLimits,
    HarnessConfig,
    default_app_data_dirs,
    load_config,
)

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

SEVERITY_DISABLED = "disabled"
SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_HIGH = "high"

TIER_SEVERITY = {
    0: SEVERITY_OK,
    1: SEVERITY_WARN,
    2: SEVERITY_HIGH,
}

# Step types and fields stored in conversations/<id>.db.
PLANNER_RESPONSE = 15
STEP_SOURCE, STEP_TOOL_CALL, STEP_MODEL_USAGE = 3, 4, 9
SOURCE_MODEL = 2
USAGE_INPUT, USAGE_OUTPUT, USAGE_CACHE_WRITE, USAGE_CACHE_READ = 2, 3, 4, 5
META_PARENT, META_ROOT = 5, 6
# gen_metadata: chat model > chat start > context window > estimated tokens.
GEN_CHAT_MODEL, CHAT_START, CONTEXT_WINDOW, ESTIMATED = 1, 9, 10, 1

TMUX_CONTEXT_SLOT_WIDTH = 12
TMUX_CONTEXT_PAD = " " * TMUX_CONTEXT_SLOT_WIDTH

_UNSET = object()


def _get_config(
    config: HarnessConfig | None,
    env: Mapping[str, str] | None = None,
) -> HarnessConfig:
  return config if config is not None else load_config(env=env)


def default_data_dirs(
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
  """Returns candidate app data directories for configured flavors."""
  cfg = _get_config(config, env=env)
  env_map = os.environ if env is None else env
  defaults = default_app_data_dirs(cfg)
  if data_dir and data_dir not in defaults:
    return [data_dir]
  env_dir = env_map.get(cfg.env_data_dir) if cfg.env_data_dir else None
  if env_dir:
    return [data_dir or os.path.expanduser(env_dir)]
  seen: list[str] = []
  for d in ([data_dir] if data_dir else []) + list(defaults):
    if d and d not in seen and os.path.isdir(d):
      seen.append(d)
  if seen:
    return seen
  return [data_dir or defaults[0]]


def default_data_dir(
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  cfg = _get_config(config, env=env)
  env_map = os.environ if env is None else env
  env_dir = env_map.get(cfg.env_data_dir) if cfg.env_data_dir else None
  if env_dir:
    return os.path.expanduser(env_dir)
  return default_data_dirs(config=cfg, env=env_map)[0]


def _load_limits_file(path: str) -> dict[str, int] | None:
  if not path or not os.path.isfile(path):
    return None
  try:
    with open(path, encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, ValueError):
    return None
  if not isinstance(data, dict):
    return None
  warn = data.get("warn")
  high = data.get("high")
  if (
      isinstance(warn, bool)
      or isinstance(high, bool)
      or not isinstance(warn, int)
      or not isinstance(high, int)
      or warn <= 0
      or high < warn
  ):
    return None
  return {"warn": warn, "high": high}


def load_context_limits(
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    limits_path: str | pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, int] | None:
  """Returns `{'warn': int, 'high': int}`, or `None` when limits are omitted."""
  env_map = os.environ if env is None else env
  if limits_path is not None:
    return _load_limits_file(str(limits_path))

  override_file = env_map.get("CODE_HARNESS_CONTEXT_LIMITS_FILE")
  if override_file:
    return _load_limits_file(os.path.expanduser(override_file))

  cfg = _get_config(config, env=env_map)
  limits: ContextLimits | None = cfg.get_context_limits(
      flavor=flavor, env=env_map
  )
  return limits.to_dict() if limits is not None else None


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
  result = shift = 0
  while True:
    if pos >= len(buf) or shift > 63:
      raise ValueError("bad varint")
    b = buf[pos]
    pos += 1
    result |= (b & 0x7F) << shift
    if not b & 0x80:
      return result, pos
    shift += 7


def proto_fields(buf: bytes | None) -> dict[int, list[Any]]:
  """Decodes one protobuf message into `{field_number: [values]}`."""
  out: dict[int, list[Any]] = {}
  pos = 0
  raw = bytes(buf or b"")
  while pos < len(raw):
    key, pos = _varint(raw, pos)
    num, wire = key >> 3, key & 7
    if wire == 0:
      val: Any
      val, pos = _varint(raw, pos)
    elif wire == 2:
      size, pos = _varint(raw, pos)
      val, pos = raw[pos : pos + size], pos + size
    elif wire in (1, 5):
      val, pos = None, pos + (8 if wire == 1 else 4)
    else:
      raise ValueError("bad wire type")
    if pos > len(raw):
      raise ValueError("truncated")
    out.setdefault(num, []).append(val)
  return out


def proto_first(msg: Mapping[int, list[Any]], num: int, default: Any = None):
  vals = msg.get(num) or []
  return vals[0] if vals else default


def open_conv_db(
    conversation_id: str | None,
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> sqlite3.Connection | None:
  """Opens `conversations/<id>.db` read-only, or returns `None`."""
  if not conversation_id or not UUID_RE.match(conversation_id):
    return None
  for base in default_data_dirs(data_dir, config=config, env=env):
    path = os.path.join(base, "conversations", f"{conversation_id}.db")
    if not os.path.isfile(path):
      continue
    uri = pathlib.Path(path).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=0.5)
  return None


def prompt_tokens(usage_blob: bytes | None) -> int:
  """Prompt size (uncached + cache read + cache write) from ModelUsageStats."""
  u = proto_fields(usage_blob)
  return sum(
      proto_first(u, f, 0) or 0
      for f in (USAGE_INPUT, USAGE_CACHE_READ, USAGE_CACHE_WRITE)
  )


def is_subagent(con: sqlite3.Connection, conversation_id: str) -> bool:
  """True when `conversation_id`'s trajectory metadata marks it a subagent."""
  row = con.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
  meta = proto_fields(row[0]) if (row and row[0]) else {}
  root_raw = proto_first(meta, META_ROOT, b"")
  root = (
      root_raw.decode("ascii", "replace")
      if isinstance(root_raw, bytes)
      else ""
  )
  return bool(proto_first(meta, META_PARENT)) or bool(
      root and root != conversation_id
  )


def turn_ended(con: sqlite3.Connection, initial_steps: int) -> bool:
  """True if the newest model step since `initial_steps` is a final reply."""
  rows = con.execute(
      "SELECT step_type, metadata FROM steps WHERE idx >= ? ORDER BY idx DESC",
      (initial_steps,),
  ).fetchall()
  for step_type, meta in rows:
    m = proto_fields(meta)
    if step_type == PLANNER_RESPONSE:
      return True
    if proto_first(m, STEP_SOURCE) == SOURCE_MODEL and proto_first(
        m, STEP_TOOL_CALL
    ):
      return False
  return False


def context_size(con: sqlite3.Connection) -> int:
  """Prompt tokens of the most recent model call, or 0 if unknown."""
  row = con.execute(
      "SELECT metadata FROM steps WHERE step_type = ? ORDER BY idx DESC "
      "LIMIT 1",
      (PLANNER_RESPONSE,),
  ).fetchone()
  if row and row[0]:
    try:
      usage = proto_first(proto_fields(row[0]), STEP_MODEL_USAGE)
      if usage:
        size = prompt_tokens(usage)
        if size:
          return size
    except (ValueError, TypeError):
      pass
  for (blob,) in con.execute(
      "SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 5"
  ):
    try:
      chat = proto_fields(proto_first(proto_fields(blob), GEN_CHAT_MODEL))
      window = proto_fields(
          proto_first(
              proto_fields(proto_first(chat, CHAT_START)), CONTEXT_WINDOW
          )
      )
      size = proto_first(window, ESTIMATED, 0)
    except (ValueError, TypeError):
      continue
    if size:
      return size
  return 0


def format_tokens(n: int) -> str:
  if n <= 0:
    return ""
  if n >= 1_000_000:
    return f"{n / 1_000_000:.1f}M"
  return f"{max(1, round(n / 1000))}K"


def format_tokens_or_zero(n: int | None) -> str:
  """Formats token count as compact label, returning '0' when zero/unset."""
  if n is None or n <= 0:
    return "0"
  if n >= 10_000_000:
    return f"{round(n / 1_000_000)}M"
  return format_tokens(n)


def format_tmux_context_badge(
    tokens: int | None,
    limits: Any = _UNSET,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  """Formats a fixed 12-column tmux status-left badge for an agent pane."""
  if limits is _UNSET:
    limits = load_context_limits(config=config, flavor=flavor, env=env)
  elif isinstance(limits, ContextLimits):
    limits = limits.to_dict()
  if limits is None:
    return ""
  tok = max(0, int(tokens or 0))
  tier = tier_of(tok, limits)
  label = format_tokens_or_zero(tok)
  inner = TMUX_CONTEXT_SLOT_WIDTH - 1
  if tier == 0:
    body = f" ctx: {label}".ljust(inner)[:inner]
    return f" #[fg=colour246]{body}#[default]"
  pill = f" ctx: {label} "
  pad = " " * max(0, inner - len(pill))
  if tier == 1:
    return f" #[fg=black,bg=colour214,bold]{pill}#[default]{pad}"
  return f" #[fg=white,bg=colour160,bold]{pill}#[default]{pad}"


def tier_of(tokens: int, limits: Mapping[str, int] | None) -> int:
  """Returns 0 (< warn), 1 ([warn, high)), or 2 (>= high), or 0 if disabled."""
  if not limits or tokens <= 0:
    return 0
  if tokens >= limits["high"]:
    return 2
  if tokens >= limits["warn"]:
    return 1
  return 0


def evaluate_context_size(
    tokens: int,
    limits: Any = _UNSET,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  """Evaluates a token count against `limits` (loaded from config if omitted)."""
  if limits is _UNSET:
    limits = load_context_limits(config=config, flavor=flavor, env=env)
  elif isinstance(limits, ContextLimits):
    limits = limits.to_dict()
  tok = max(0, int(tokens or 0))
  formatted = format_tokens(tok)
  if limits is None:
    return {
        "enabled": False,
        "tokens": tok,
        "formatted": formatted,
        "tier": 0,
        "severity": SEVERITY_DISABLED,
        "limits": None,
    }
  tier = tier_of(tok, limits)
  return {
      "enabled": True,
      "tokens": tok,
      "formatted": formatted,
      "tier": tier,
      "severity": TIER_SEVERITY[tier],
      "limits": dict(limits),
  }


def inspect_conversation_context(
    conversation_id: str | None,
    data_dir: str | None = None,
    limits: Any = _UNSET,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  """Reads `conversation_id`'s context size and evaluates severity."""
  if limits is _UNSET:
    limits = load_context_limits(config=config, flavor=flavor, env=env)
  con = open_conv_db(
      conversation_id, data_dir=data_dir, config=config, env=env
  )
  if con is None:
    res = evaluate_context_size(
        0, limits=limits, config=config, flavor=flavor, env=env
    )
    res["is_subagent"] = False
    res["has_db"] = False
    return res
  try:
    sub = is_subagent(con, conversation_id or "")
    size = context_size(con)
  except (sqlite3.Error, ValueError):
    sub = False
    size = 0
  finally:
    con.close()
  res = evaluate_context_size(
      size, limits=limits, config=config, flavor=flavor, env=env
  )
  res["is_subagent"] = sub
  res["has_db"] = True
  return res


def _parse_context_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness context",
      description=(
          "Inspect a conversation's prompt token count and configured "
          "context warning tier as JSON."
      ),
  )
  p.add_argument(
      "--data-dir",
      help="Override the coding-agent data directory.",
  )
  p.add_argument(
      "--limits-file",
      help="Override context-limits JSON file path.",
  )
  p.add_argument(
      "--check-turn-ended",
      type=int,
      metavar="INITIAL_STEPS",
      help="Include boolean `turn_ended` since INITIAL_STEPS in output.",
  )
  p.add_argument(
      "conversation_id",
      nargs="?",
      default="",
      help="Conversation UUID to inspect (omit to print configured limits).",
  )
  return p.parse_args(argv)


def cmd_context(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = _parse_context_args(argv)
  limits = load_context_limits(
      config=config, flavor=flavor, limits_path=args.limits_file
  )
  if not args.conversation_id:
    print(
        json.dumps(
            {"enabled": limits is not None, "limits": limits},
            indent=2,
        )
    )
    return 0

  result = inspect_conversation_context(
      args.conversation_id,
      data_dir=args.data_dir,
      limits=limits,
      config=config,
      flavor=flavor,
  )
  if args.check_turn_ended is not None:
    con = open_conv_db(
        args.conversation_id, data_dir=args.data_dir, config=config
    )
    ended = False
    if con is not None:
      try:
        ended = turn_ended(con, args.check_turn_ended)
      except (sqlite3.Error, ValueError):
        ended = False
      finally:
        con.close()
    result["turn_ended"] = ended

  print(json.dumps(result, indent=2))
  return 0

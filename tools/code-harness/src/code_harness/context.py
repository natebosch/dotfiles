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
import time
from typing import Any, Iterable, Mapping

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

# Step types, statuses, and fields stored in conversations/<id>.db.
STEP_FINISH = 2
STEP_USER_INPUT = 14
PLANNER_RESPONSE = 15
STEP_ERROR_MESSAGE = 17
STEP_CHECKPOINT = 23
STEP_SYSTEM_MESSAGE = 101
STEP_GENERIC = 132

STATUS_PENDING = 1
STATUS_RUNNING = 2
STATUS_DONE = 3
STATUS_CANCELED = 6
STATUS_GENERATING = 8
STATUS_WAITING = 9
STATUS_QUEUED = 11
STATUS_INTERRUPTED = 12

ACTIVE_STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_GENERATING,
    STATUS_WAITING,
    STATUS_QUEUED,
)
HOUSEKEEPING_STEPS = frozenset({
    STEP_SYSTEM_MESSAGE,
    STEP_CHECKPOINT,
    STEP_FINISH,
})

STEP_META_CREATED_AT = 1
TIMESTAMP_SECONDS = 1
STEP_SOURCE, STEP_TOOL_CALL, STEP_MODEL_USAGE = 3, 4, 9
SOURCE_MODEL = 2
USAGE_INPUT, USAGE_OUTPUT, USAGE_CACHE_WRITE, USAGE_CACHE_READ = 2, 3, 4, 5
META_PARENT, META_ROOT, META_SUBAGENT_SPEC = 5, 6, 8
SPEC_TYPE_NAME, SPEC_ROLE = 1, 2
TASK_DETAILS_IS_DAEMON = 6
# Generic step AgentRef path: Step.generic > result > render_info > blocks > agent.
GEN_STEP_GENERIC, GEN_RESULT, GEN_RENDER_INFO = 140, 2, 3
GEN_CONTENT_BLOCKS, GEN_BLOCK_AGENT, AGENT_REF_CONV_ID = 10, 4, 1
# gen_metadata: chat model > chat start > context window > estimated tokens.
GEN_CHAT_MODEL, CHAT_START, CONTEXT_WINDOW, ESTIMATED = 1, 9, 10, 1

MAX_BFS_CHILDREN = 64
MAX_ROOT_HOPS = 8
RECENT_STEP_SEC = 60

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


def _find_conv_db_path(
    conversation_id: str | None,
    search_dirs: Iterable[str] | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
  if not conversation_id or not UUID_RE.match(conversation_id):
    return None, None
  dirs = (
      list(search_dirs)
      if search_dirs is not None
      else default_data_dirs(config=config, env=env)
  )
  for base in dirs:
    path = os.path.join(base, "conversations", f"{conversation_id}.db")
    if os.path.isfile(path):
      return path, base
  return None, None


def _open_conv_db_with_dir(
    conversation_id: str | None,
    search_dirs: Iterable[str] | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[sqlite3.Connection | None, str | None, str | None]:
  path, base = _find_conv_db_path(
      conversation_id, search_dirs=search_dirs, config=config, env=env
  )
  if path is None:
    return None, None, None
  uri = pathlib.Path(path).as_uri() + "?mode=ro"
  return sqlite3.connect(uri, uri=True, timeout=0.5), path, base


def _ordered_search_dirs(
    data_dir: str | None = None,
    preferred_dir: str | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
  base_dirs = default_data_dirs(data_dir, config=config, env=env)
  if not preferred_dir:
    return base_dirs
  return [preferred_dir] + [d for d in base_dirs if d != preferred_dir]


def open_conv_db(
    conversation_id: str | None,
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> sqlite3.Connection | None:
  """Opens `conversations/<id>.db` read-only, or returns `None`."""
  con, _, _ = _open_conv_db_with_dir(
      conversation_id,
      search_dirs=default_data_dirs(data_dir, config=config, env=env),
      config=config,
      env=env,
  )
  return con


def _decode_str(raw: Any) -> str:
  if isinstance(raw, bytes):
    return raw.decode("utf-8", "replace")
  return ""


def _read_trajectory_meta(
    con: sqlite3.Connection,
) -> tuple[str, str, str, str]:
  """Returns `(parent_id, root_id, type_name, role)` from metadata blob."""
  try:
    row = con.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
    meta = proto_fields(row[0]) if (row and row[0]) else {}
  except (sqlite3.Error, ValueError):
    return "", "", "", ""
  parent = _decode_str(proto_first(meta, META_PARENT, b""))
  root = _decode_str(proto_first(meta, META_ROOT, b""))
  type_name = ""
  role = ""
  spec_raw = proto_first(meta, META_SUBAGENT_SPEC)
  if isinstance(spec_raw, bytes) and spec_raw:
    try:
      spec = proto_fields(spec_raw)
      type_name = _decode_str(proto_first(spec, SPEC_TYPE_NAME, b""))
      role = _decode_str(proto_first(spec, SPEC_ROLE, b""))
    except ValueError:
      pass
  return parent, root, type_name, role


def prompt_tokens(usage_blob: bytes | None) -> int:
  """Prompt size (uncached + cache read + cache write) from ModelUsageStats."""
  u = proto_fields(usage_blob)
  return sum(
      proto_first(u, f, 0) or 0
      for f in (USAGE_INPUT, USAGE_CACHE_READ, USAGE_CACHE_WRITE)
  )


def is_subagent(con: sqlite3.Connection, conversation_id: str) -> bool:
  """True when `conversation_id`'s trajectory metadata marks it a subagent."""
  parent, root, _, _ = _read_trajectory_meta(con)
  return bool(parent) or bool(root and root != conversation_id)


def compaction_count(con: sqlite3.Connection) -> int:
  """Returns completed checkpoint (compaction) step count, or 0 on error."""
  try:
    row = con.execute(
        "SELECT COUNT(*) FROM steps WHERE step_type = ? AND status = ?",
        (STEP_CHECKPOINT, STATUS_DONE),
    ).fetchone()
    return int(row[0] or 0) if row else 0
  except sqlite3.Error:
    return 0


def resolve_root_conversation_id(
    conversation_id: str | None,
    data_dir: str | None = None,
    con: sqlite3.Connection | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  """Resolves the root conversation UUID for `conversation_id`."""
  if not conversation_id or not UUID_RE.match(conversation_id):
    return ""
  cur_id = conversation_id
  cur_con = con
  visited = {conversation_id}
  for hop in range(MAX_ROOT_HOPS + 1):
    opened = None
    if cur_con is None:
      opened = open_conv_db(
          cur_id, data_dir=data_dir, config=config, env=env
      )
      cur_con = opened
    if cur_con is None:
      return cur_id
    try:
      parent, root, _, _ = _read_trajectory_meta(cur_con)
    finally:
      if opened is not None:
        opened.close()
    if root and UUID_RE.match(root) and root != cur_id:
      return root
    if (
        hop < MAX_ROOT_HOPS
        and parent
        and UUID_RE.match(parent)
        and parent not in visited
    ):
      visited.add(parent)
      cur_id = parent
      cur_con = None
      continue
    return cur_id
  return cur_id


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
  try:
    row = con.execute(
        "SELECT metadata FROM steps WHERE step_type = ? ORDER BY idx DESC "
        "LIMIT 1",
        (PLANNER_RESPONSE,),
    ).fetchone()
  except sqlite3.Error:
    row = None
  if row and row[0]:
    try:
      usage = proto_first(proto_fields(row[0]), STEP_MODEL_USAGE)
      if usage:
        size = prompt_tokens(usage)
        if size:
          return size
    except (ValueError, TypeError):
      pass
  try:
    gen_rows = con.execute(
        "SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 5"
    ).fetchall()
  except sqlite3.Error:
    return 0
  for (blob,) in gen_rows:
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


def _steps_columns(con: sqlite3.Connection | None) -> set[str]:
  if con is None:
    return set()
  try:
    return {r[1] for r in con.execute("PRAGMA table_info(steps)").fetchall()}
  except sqlite3.Error:
    return set()


def _proto_submsgs(
    msgs: list[dict[int, list[Any]]], field: int
) -> list[dict[int, list[Any]]]:
  out: list[dict[int, list[Any]]] = []
  for msg in msgs:
    for raw in msg.get(field) or ():
      if not isinstance(raw, bytes):
        continue
      try:
        out.append(proto_fields(raw))
      except ValueError:
        continue
  return out


def _workflow_agent_ids_from_step(step_payload: bytes | None) -> list[str]:
  if not step_payload:
    return []
  try:
    msgs = [proto_fields(step_payload)]
  except ValueError:
    return []
  for field in (
      GEN_STEP_GENERIC,
      GEN_RESULT,
      GEN_RENDER_INFO,
      GEN_CONTENT_BLOCKS,
      GEN_BLOCK_AGENT,
  ):
    msgs = _proto_submsgs(msgs, field)
    if not msgs:
      return []
  ids: list[str] = []
  seen: set[str] = set()
  for ag in msgs:
    for raw in ag.get(AGENT_REF_CONV_ID) or ():
      cid = _decode_str(raw)
      if UUID_RE.match(cid) and cid not in seen:
        seen.add(cid)
        ids.append(cid)
  return ids


def _active_workflow_children(
    con: sqlite3.Connection | None, cols: set[str] | None = None
) -> list[str]:
  if con is None:
    return []
  cols = cols if cols is not None else _steps_columns(con)
  if "step_payload" not in cols:
    return []
  try:
    rows = con.execute(
        "SELECT step_payload FROM steps "
        "WHERE step_type = ? AND status IN (?, ?, ?, ?, ?)",
        (STEP_GENERIC, *ACTIVE_STATUSES),
    ).fetchall()
  except sqlite3.Error:
    return []
  ids: list[str] = []
  seen: set[str] = set()
  for (payload,) in rows:
    for cid in _workflow_agent_ids_from_step(payload):
      if cid not in seen:
        seen.add(cid)
        ids.append(cid)
  return ids


def _swarm_roster(
    cur_id: str, search_dirs: Iterable[str]
) -> tuple[dict[str, dict[str, str]], set[str]]:
  """Returns `(swarm_live_dict, killed_set)` from swarm/members.json."""
  swarm_live: dict[str, dict[str, str]] = {}
  killed: set[str] = set()
  for base in search_dirs:
    path = os.path.join(
        base, "brain", cur_id, ".system_generated", "swarm", "members.json"
    )
    if not os.path.isfile(path):
      continue
    try:
      with open(path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    except (OSError, ValueError):
      continue
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, dict):
      continue
    for name, info in sorted(members.items()):
      if not isinstance(info, dict):
        continue
      cid = info.get("conversation_id") or ""
      if not UUID_RE.match(cid) or cid == cur_id:
        continue
      phase = str(info.get("phase") or "").upper()
      desired = str(info.get("desired_state") or "").upper()
      if phase == "KILLED" or desired == "KILLED":
        killed.add(cid)
        swarm_live.pop(cid, None)
      elif cid not in killed:
        swarm_live[cid] = {"role": str(info.get("role") or name)}
  return swarm_live, killed


def _subagent_registry(
    cur_id: str, search_dirs: Iterable[str]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
  """Returns `(alive_dict, killed_set)` from subagents/*.json."""
  entries: dict[str, dict[str, Any]] = {}
  killed: set[str] = set()
  for base in search_dirs:
    sub_dir = os.path.join(
        base, "brain", cur_id, ".system_generated", "subagents"
    )
    if not os.path.isdir(sub_dir):
      continue
    try:
      names = sorted(os.listdir(sub_dir))
    except OSError:
      continue
    for name in names:
      if not name.endswith(".json"):
        continue
      path = os.path.join(sub_dir, name)
      try:
        with open(path, encoding="utf-8", errors="replace") as f:
          data = json.load(f)
      except (OSError, ValueError):
        continue
      if not isinstance(data, dict):
        continue
      cid = data.get("conversationId") or ""
      if not UUID_RE.match(cid) or cid == cur_id:
        continue
      if data.get("state") == "SUBAGENT_STATE_KILLED":
        killed.add(cid)
        entries.pop(cid, None)
        continue
      if cid in killed:
        continue
      desc = data.get("subagentDescriptor")
      if not isinstance(desc, dict):
        desc = {}
      raw_spawn = data.get("spawnStepIndex", -1)
      spawn_idx = (
          raw_spawn
          if isinstance(raw_spawn, int) and not isinstance(raw_spawn, bool)
          else -1
      )
      entries[cid] = {
          "type_name": str(desc.get("typeName") or ""),
          "role": str(desc.get("role") or ""),
          "spawn_step_index": spawn_idx,
      }
  return entries, killed


def _is_killed_in_registry(
    parent_id: str, child_id: str, search_dirs: Iterable[str]
) -> bool:
  """Checks whether `parent_id`'s subagents or swarm marks `child_id` killed."""
  if not parent_id or not UUID_RE.match(parent_id):
    return False
  for base in search_dirs:
    sub_path = os.path.join(
        base,
        "brain",
        parent_id,
        ".system_generated",
        "subagents",
        f"{child_id}.json",
    )
    if os.path.isfile(sub_path):
      try:
        with open(sub_path, encoding="utf-8", errors="replace") as f:
          data = json.load(f)
        if (
            isinstance(data, dict)
            and data.get("state") == "SUBAGENT_STATE_KILLED"
        ):
          return True
      except (OSError, ValueError):
        pass
    swarm_path = os.path.join(
        base, "brain", parent_id, ".system_generated", "swarm", "members.json"
    )
    if os.path.isfile(swarm_path):
      try:
        with open(swarm_path, encoding="utf-8", errors="replace") as f:
          data = json.load(f)
        members = data.get("members") if isinstance(data, dict) else None
        if isinstance(members, dict):
          for info in members.values():
            if not isinstance(info, dict):
              continue
            if info.get("conversation_id") != child_id:
              continue
            phase = str(info.get("phase") or "").upper()
            desired = str(info.get("desired_state") or "").upper()
            if phase == "KILLED" or desired == "KILLED":
              return True
      except (OSError, ValueError):
        pass
  return False


def _is_daemon_task(task_blob: bytes | None) -> bool:
  if not task_blob:
    return False
  try:
    msg = proto_fields(task_blob)
    return bool(proto_first(msg, TASK_DETAILS_IS_DAEMON, 0))
  except ValueError:
    return False


def _step_created_epoch(meta_blob: bytes | None) -> int:
  if not meta_blob:
    return 0
  try:
    meta = proto_fields(meta_blob)
    ts_raw = proto_first(meta, STEP_META_CREATED_AT)
    if not isinstance(ts_raw, bytes):
      return 0
    ts_msg = proto_fields(ts_raw)
    secs = proto_first(ts_msg, TIMESTAMP_SECONDS, 0) or 0
    return secs if isinstance(secs, int) and secs > 0 else 0
  except ValueError:
    return 0


def _db_wal_mtime(db_path: str | None) -> float:
  if not db_path:
    return 0.0
  best = 0.0
  for path in (db_path, db_path + "-wal"):
    try:
      mt = os.path.getmtime(path)
      if mt > best:
        best = mt
    except OSError:
      pass
  return best


def _latest_step_epoch(
    con: sqlite3.Connection | None, cols: set[str]
) -> int:
  if con is None or "metadata" not in cols:
    return 0
  try:
    row = con.execute(
        "SELECT metadata FROM steps ORDER BY idx DESC LIMIT 1"
    ).fetchone()
    return _step_created_epoch(row[0]) if row else 0
  except sqlite3.Error:
    return 0


def _is_trajectory_running(
    child_con: sqlite3.Connection | None,
    child_db_path: str | None,
    active_hint: bool = False,
    cols: set[str] | None = None,
) -> bool:
  """True when `child_con` appears to have in-flight work."""
  if active_hint:
    return True
  if child_con is None:
    return False
  cols = cols if cols is not None else _steps_columns(child_con)
  try:
    latest = child_con.execute(
        "SELECT idx FROM steps ORDER BY idx DESC LIMIT 1"
    ).fetchone()
    if latest is None:
      return False
    max_idx = latest[0]
    ui_row = child_con.execute(
        "SELECT MAX(idx) FROM steps WHERE step_type = ?",
        (STEP_USER_INPUT,),
    ).fetchone()
    last_ui_idx = ui_row[0] if (ui_row and ui_row[0] is not None) else -1
    min_idx = min(max(0, last_ui_idx), max(0, max_idx - 25))
    ckpt_row = child_con.execute(
        "SELECT COALESCE(MAX(idx), 0) FROM steps "
        "WHERE step_type = ? AND status = ?",
        (STEP_CHECKPOINT, STATUS_DONE),
    ).fetchone()
    ckpt_idx = ckpt_row[0] if (ckpt_row and ckpt_row[0] is not None) else 0
    lower_idx = min(min_idx, ckpt_idx)
    td_col = (
        "task_details" if "task_details" in cols else "NULL AS task_details"
    )
    raw_rows = child_con.execute(
        f"SELECT idx, status, {td_col} FROM steps "
        "WHERE idx >= ? AND status IN (?, ?, ?, ?, ?)",
        (lower_idx, *ACTIVE_STATUSES),
    ).fetchall()
    for row_idx, st_val, task_blob in raw_rows:
      if st_val == STATUS_WAITING:
        keep = row_idx >= ckpt_idx
      elif task_blob:
        keep = row_idx >= ckpt_idx and not _is_daemon_task(task_blob)
      else:
        keep = row_idx >= min_idx
      if keep:
        return True

    md_col = "metadata" if "metadata" in cols else "NULL AS metadata"
    tail_rows = child_con.execute(
        f"SELECT step_type, status, {md_col} FROM steps "
        "ORDER BY idx DESC LIMIT 10"
    ).fetchall()
  except sqlite3.Error:
    return False

  eff = next(
      (
          (t, s, m)
          for t, s, m in tail_rows
          if not (s == STATUS_DONE and t in HOUSEKEEPING_STEPS)
      ),
      None,
  )
  if eff is None:
    return False
  eff_type, eff_status, eff_meta = eff
  if (
      eff_type == PLANNER_RESPONSE
      or eff_status in (STATUS_CANCELED, STATUS_INTERRUPTED)
      or eff_type == STEP_ERROR_MESSAGE
  ):
    return False
  step_ts = _step_created_epoch(eff_meta)
  eff_ts = step_ts if step_ts > 0 else _db_wal_mtime(child_db_path)
  return eff_ts > 0 and (time.time() - eff_ts) < RECENT_STEP_SEC


def _discover_children(
    root_id: str,
    root_con: sqlite3.Connection,
    search_dirs: list[str],
    extra_child_ids: Iterable[str] = (),
    active_child_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
  """BFS over live subagents, warm worker, swarm members, and workflows."""
  extra_valid = [
      cid for cid in (extra_child_ids or ()) if cid and UUID_RE.match(cid)
  ]
  active_valid = [
      cid for cid in (active_child_ids or ()) if cid and UUID_RE.match(cid)
  ]
  active_set = set(active_valid)

  seen_children: set[str] = set()
  killed_ids: set[str] = set()
  children: list[dict[str, Any]] = []
  queue = [root_id]

  while queue and len(children) < MAX_BFS_CHILDREN:
    cur_id = queue.pop(0)
    opened_cur = None
    cur_con: sqlite3.Connection | None = (
        root_con if cur_id == root_id else None
    )
    if cur_con is None:
      opened_cur, _, _ = _open_conv_db_with_dir(
          cur_id, search_dirs=search_dirs
      )
      cur_con = opened_cur

    try:
      cur_cols = _steps_columns(cur_con)
      wf_ids = _active_workflow_children(cur_con, cols=cur_cols)
    finally:
      if opened_cur is not None:
        opened_cur.close()

    wf_set = set(wf_ids)
    swarm_live, swarm_killed = _swarm_roster(cur_id, search_dirs)
    killed_ids.update(swarm_killed)

    sub_entries, sub_killed = _subagent_registry(cur_id, search_dirs)
    killed_ids.update(sub_killed)
    for kcid in sub_killed:
      swarm_live.pop(kcid, None)

    cands: dict[str, dict[str, Any]] = {}
    for cid, sinfo in swarm_live.items():
      if cid == root_id or cid in killed_ids or cid in seen_children:
        continue
      cands[cid] = {
          "type_name": "swarm",
          "role": sinfo["role"],
          "spawn_step_index": -1,
          "wf_active": cid in wf_set,
          "parent_key": cur_id,
          "con": None,
          "db_path": None,
          "meta": None,
      }

    for cid, entry in sub_entries.items():
      if cid == root_id or cid in killed_ids or cid in seen_children:
        continue
      if cid in cands:
        cands[cid]["role"] = cands[cid]["role"] or entry["role"]
        cands[cid]["spawn_step_index"] = entry["spawn_step_index"]
      else:
        cands[cid] = {
            "type_name": entry["type_name"],
            "role": entry["role"],
            "spawn_step_index": entry["spawn_step_index"],
            "wf_active": cid in wf_set,
            "parent_key": cur_id,
            "con": None,
            "db_path": None,
            "meta": None,
        }

    for cid in wf_ids:
      if cid == root_id or cid in killed_ids or cid in seen_children:
        continue
      if cid in cands:
        cands[cid]["wf_active"] = True
      else:
        cands[cid] = {
            "type_name": "workflow",
            "role": "",
            "spawn_step_index": -1,
            "wf_active": True,
            "parent_key": cur_id,
            "con": None,
            "db_path": None,
            "meta": None,
        }

    if cur_id == root_id:
      for cid in (*extra_valid, *active_valid):
        if (
            cid == root_id
            or cid in cands
            or cid in killed_ids
            or cid in seen_children
        ):
          continue
        c_con, c_path, _ = _open_conv_db_with_dir(
            cid, search_dirs=search_dirs
        )
        if c_con is None:
          continue
        meta_parent, meta_root, spec_type, spec_role = _read_trajectory_meta(
            c_con
        )
        if meta_parent != root_id and meta_root != root_id:
          c_con.close()
          continue
        if (
            meta_parent
            and meta_parent != root_id
            and _is_killed_in_registry(meta_parent, cid, search_dirs)
        ):
          killed_ids.add(cid)
          c_con.close()
          continue
        if meta_root == root_id and _is_killed_in_registry(
            meta_root, cid, search_dirs
        ):
          killed_ids.add(cid)
          c_con.close()
          continue
        if not spec_type and not meta_parent and meta_root == root_id:
          spec_type = "workflow"
        parent_key = meta_parent if UUID_RE.match(meta_parent) else cur_id
        cands[cid] = {
            "type_name": spec_type,
            "role": spec_role,
            "spawn_step_index": -1,
            "wf_active": False,
            "parent_key": parent_key,
            "con": c_con,
            "db_path": c_path,
            "meta": (meta_parent, meta_root, spec_type, spec_role),
        }

    evaluated: list[tuple[str, dict[str, Any]]] = []
    for cid, cand in cands.items():
      c_con = cand["con"]
      c_path = cand["db_path"]
      if c_con is None:
        c_con, c_path, _ = _open_conv_db_with_dir(
            cid, search_dirs=search_dirs
        )
      try:
        eff_type = cand["type_name"]
        eff_role = cand["role"]
        if c_con is not None and (not eff_type or not eff_role):
          meta = cand["meta"] or _read_trajectory_meta(c_con)
          if not eff_type:
            eff_type = meta[2] or (
                "workflow" if (not meta[0] and meta[1] == root_id) else ""
            )
          if not eff_role:
            eff_role = meta[3]
        c_cols = _steps_columns(c_con)
        active_hint = (cid in active_set) or cand["wf_active"]
        running = _is_trajectory_running(
            c_con, c_path, active_hint=active_hint, cols=c_cols
        )
        step_epoch = _latest_step_epoch(c_con, c_cols)
        mtime = _db_wal_mtime(c_path)
        c_tokens = context_size(c_con) if c_con is not None else 0
        c_compactions = compaction_count(c_con) if c_con is not None else 0
      finally:
        if c_con is not None:
          c_con.close()

      evaluated.append((
          cid,
          {
              "type": eff_type,
              "role": eff_role,
              "tokens": c_tokens,
              "compactions": c_compactions,
              "running": running,
              "step_epoch": step_epoch,
              "spawn_step_index": cand["spawn_step_index"],
              "mtime": mtime,
              "parent_key": cand["parent_key"],
          },
      ))

    warm_workers: dict[str, str] = {}
    warm_ranks: dict[str, tuple[int, int, float]] = {}
    for cid, item in evaluated:
      if cid not in swarm_live and item["type"] == "worker":
        pkey = item["parent_key"]
        rank = (item["step_epoch"], item["spawn_step_index"], item["mtime"])
        if pkey not in warm_ranks or rank > warm_ranks[pkey]:
          warm_ranks[pkey] = rank
          warm_workers[pkey] = cid

    for cid, item in evaluated:
      is_warm = warm_workers.get(item["parent_key"]) == cid
      if not (cid in swarm_live or item["running"] or is_warm):
        continue
      if cid in seen_children:
        continue
      if len(children) >= MAX_BFS_CHILDREN:
        break
      seen_children.add(cid)
      children.append({
          "id": cid,
          "type": item["type"],
          "role": item["role"],
          "tokens": item["tokens"],
          "formatted": format_tokens(item["tokens"]),
          "compactions": item["compactions"],
          "running": item["running"],
      })
      queue.append(cid)

  return children


def inspect_conversation_context(
    conversation_id: str | None,
    data_dir: str | None = None,
    limits: Any = _UNSET,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
    extra_child_ids: Iterable[str] = (),
    active_child_ids: Iterable[str] = (),
) -> dict[str, Any]:
  """Reads `conversation_id` (and live/warm children) and evaluates severity."""
  if limits is _UNSET:
    limits = load_context_limits(config=config, flavor=flavor, env=env)
  valid_id = (
      conversation_id
      if (conversation_id and UUID_RE.match(conversation_id))
      else ""
  )
  con, _, db_data_dir = _open_conv_db_with_dir(
      conversation_id,
      search_dirs=default_data_dirs(data_dir, config=config, env=env),
      config=config,
      env=env,
  )
  if con is None:
    res = evaluate_context_size(
        0, limits=limits, config=config, flavor=flavor, env=env
    )
    res.update({
        "is_subagent": False,
        "has_db": False,
        "root_id": valid_id,
        "self_tokens": 0,
        "self_formatted": "",
        "child_tokens": 0,
        "child_formatted": "",
        "child_count": 0,
        "compactions": 0,
        "self_compactions": 0,
        "child_compactions": 0,
        "children": [],
    })
    return res

  search_dirs = _ordered_search_dirs(
      data_dir=data_dir, preferred_dir=db_data_dir, config=config, env=env
  )
  try:
    sub = is_subagent(con, conversation_id or "")
    self_tokens = context_size(con)
    self_compactions = compaction_count(con)
    if sub:
      root_id = resolve_root_conversation_id(
          conversation_id,
          data_dir=db_data_dir or data_dir,
          con=con,
          config=config,
          env=env,
      )
      res = evaluate_context_size(
          self_tokens, limits=limits, config=config, flavor=flavor, env=env
      )
      res.update({
          "is_subagent": True,
          "has_db": True,
          "root_id": root_id,
          "self_tokens": self_tokens,
          "self_formatted": format_tokens(self_tokens),
          "child_tokens": 0,
          "child_formatted": "",
          "child_count": 0,
          "compactions": self_compactions,
          "self_compactions": self_compactions,
          "child_compactions": 0,
          "children": [],
      })
      return res
    children = _discover_children(
        valid_id,
        con,
        search_dirs,
        extra_child_ids=extra_child_ids,
        active_child_ids=active_child_ids,
    )
  except (sqlite3.Error, ValueError):
    sub = False
    self_tokens = 0
    self_compactions = 0
    children = []
  finally:
    con.close()

  child_tokens = sum(c["tokens"] for c in children)
  total_tokens = self_tokens + child_tokens
  child_compactions = sum(c["compactions"] for c in children)
  compactions = self_compactions + child_compactions

  res = evaluate_context_size(
      total_tokens, limits=limits, config=config, flavor=flavor, env=env
  )
  res.update({
      "is_subagent": sub,
      "has_db": True,
      "root_id": valid_id,
      "self_tokens": self_tokens,
      "self_formatted": format_tokens(self_tokens),
      "child_tokens": child_tokens,
      "child_formatted": format_tokens(child_tokens),
      "child_count": len(children),
      "compactions": compactions,
      "self_compactions": self_compactions,
      "child_compactions": child_compactions,
      "children": children,
  })
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

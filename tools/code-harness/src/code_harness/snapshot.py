"""Scan tmux for coding-agent CLI sessions, snapshot layout, and close cleanly.

Finds open coding-agent CLI instances across active tmux sessions, checks
whether each conversation has settled waiting for user input or is actively
working, verifies non-agent panes are sitting idle at a shell, writes an
executable restore script preserving tmux sessions/windows/layouts, and closes
cleanly.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Mapping

from code_harness.config import (
    CliNotFoundError,
    FlavorConfig,
    HarnessConfig,
    default_app_data_dirs,
    is_excluded_path,
    load_config,
    ordered_flavors,
    resolve_cli_bin,
)

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
LOCK_RE = re.compile(r"/presence/([0-9a-fA-F-]{36})\.lock$")
DB_RE = re.compile(r"/conversations/([0-9a-fA-F-]{36})\.db$")
RESUME_LOG_RE = re.compile(r"Resuming conversation ([0-9a-fA-F-]{36})")
ISO_FRAC_RE = re.compile(r"\.(\d+)(?=[+-]|$)")
SWARM_WAIT_OUTPUT_RE = re.compile(
    r"^Waiting for [^\n]*\bposted after #\d+\.$"
)
GENERIC_TS_HEADER_RE = re.compile(
    r"^(?:(?:Created|Completed) At: [^\r\n]*\r?\n)+"
)

SHELL_NAMES = frozenset(
    {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"}
)

# Trajectory SQLite protobuf field & enum constants.
META_PARENT = 5
META_ROOT = 6

STEP_TYPE_FINISH = 2
STEP_TYPE_CODE_ACTION = 5
STEP_TYPE_USER_INPUT = 14
STEP_TYPE_PLANNER_RESPONSE = 15
STEP_TYPE_ERROR_MESSAGE = 17
STEP_TYPE_CHECKPOINT = 23
STEP_TYPE_SYSTEM_MESSAGE = 101
STEP_TYPE_GENERIC = 132
STEP_TYPE_ASK_QUESTION = 138

STATUS_PENDING = 1
STATUS_RUNNING = 2
STATUS_DONE = 3
STATUS_CANCELED = 6
STATUS_GENERATING = 8
STATUS_WAITING = 9
STATUS_QUEUED = 11
STATUS_INTERRUPTED = 12

TASK_DETAILS_DESCRIPTION = 4
TASK_DETAILS_IS_DAEMON = 6
TASK_DETAILS_TITLE = 7

STEP_CODE_ACTION = 10
CODE_ACTION_SPEC = 1
ACTION_SPEC_SUBFIELDS = (1, 2, 3)
ACTION_PATH_FIELDS = (1, 4)
CODE_ACTION_IS_ARTIFACT_FILE = 21
CODE_ACTION_ARTIFACT_METADATA = 23

STEP_GENERIC = 140
GENERIC_RESULT = 2
GENERIC_STEP_RENDER_INFO = 3
GENERIC_RESULT_PAYLOAD = 6
ANY_TYPE_URL = 1
ANY_VALUE = 2
FILE_EDIT_PATH = 2
FILE_EDIT_ARTIFACT_METADATA = 5
FILE_EDIT_IS_ARTIFACT_FILE = 6
ARTIFACT_METADATA_REQUEST_FEEDBACK = 6
RENDER_INFO_CONTENT_BLOCKS = 10
CONTENT_BLOCK_AGENT = 4
AGENT_REF_CONVERSATION_ID = 1

STEP_REQUESTED_INTERACTION = 56
INTERACTION_ELICITATION = 20
INTERACTION_PERMISSION = 21
INTERACTION_ASK_QUESTION = 22

CLI_STATE_SETTLED = "settled"
CLI_STATE_PERMISSION = "permission"
CLI_STATE_QUESTION = "question"
CLI_STATE_PLAN = "plan"
CLI_STATE_WORKING = "working"
CLI_URGENT_STATES = frozenset({CLI_STATE_PERMISSION, CLI_STATE_QUESTION})
CLI_SETTLED_STATES = frozenset({CLI_STATE_SETTLED, CLI_STATE_PLAN})


def is_urgent_cli_state(state: str) -> bool:
  return state in CLI_URGENT_STATES


def classify_ui_category(
    state: str, reason: str, conv_id: str | None
) -> str:
  """Maps CLI state metadata into perm/ask/plan/done/new/work."""
  if state == CLI_STATE_PERMISSION:
    return "perm"
  if state == CLI_STATE_QUESTION:
    return "ask"
  if state == CLI_STATE_PLAN:
    return "plan"
  if state == CLI_STATE_SETTLED:
    if not conv_id or reason in (
        "empty prompt (no conversation yet)",
        "empty conversation",
    ):
      return "new"
    return "done"
  return "work"


def _get_config(config: HarnessConfig | None) -> HarnessConfig:
  return config if config is not None else load_config()


def find_tmux_bin() -> str:
  nix_tmux = os.path.expanduser("~/.nix-profile/bin/tmux")
  if os.access(nix_tmux, os.X_OK):
    return nix_tmux
  return shutil.which("tmux") or "tmux"


def find_cli_bin(
    config: HarnessConfig | None = None,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    use_global_env: bool = True,
) -> str:
  """Resolves an executable CLI binary for `flavor` with safe fallback."""
  cfg = _get_config(config)
  env_map = dict(os.environ if env is None else env)
  if not use_global_env and cfg.env_bin:
    env_map.pop(cfg.env_bin, None)

  try:
    return resolve_cli_bin(cfg, flavor=flavor, env=env_map)
  except CliNotFoundError:
    pass

  if flavor is None:
    for flv in ordered_flavors(cfg):
      try:
        return resolve_cli_bin(cfg, flavor=flv.name, env=env_map)
      except CliNotFoundError:
        continue

  selected = cfg.get_flavor(flavor=flavor, env=env_map)
  if selected.candidate_bins:
    return os.path.expanduser(selected.candidate_bins[-1])
  return selected.name


def harness_data_dirs(
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
  """Returns candidate app data directories for configured flavors."""
  cfg = _get_config(config)
  env_map = os.environ if env is None else env
  defaults = default_app_data_dirs(cfg)
  if data_dir and data_dir not in defaults:
    return [data_dir]
  app_dir = env_map.get(cfg.env_data_dir) if cfg.env_data_dir else None
  if app_dir:
    return [data_dir or os.path.expanduser(app_dir)]
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
  cfg = _get_config(config)
  env_map = os.environ if env is None else env
  env_val = env_map.get(cfg.env_data_dir) if cfg.env_data_dir else None
  if env_val:
    return os.path.expanduser(env_val)
  return harness_data_dirs(config=cfg, env=env_map)[0]


def default_state_dir(env: Mapping[str, str] | None = None) -> str:
  env_map = os.environ if env is None else env
  xdg = env_map.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
  return os.path.join(xdg, "code-harness-tmux")


class TmuxRunner:

  def __init__(self, tmux_bin: str, socket_path: str | None = None):
    self.base = [tmux_bin]
    self.socket_path = socket_path
    if socket_path:
      self.base.extend(["-S", socket_path])

  def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*self.base, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=check,
    )


# --- Minimal protobuf decoder for trajectory SQLite blobs ---


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


def _proto_fields(buf: bytes | None) -> dict[int, list]:
  out: dict[int, list] = {}
  pos = 0
  raw = bytes(buf or b"")
  while pos < len(raw):
    key, pos = _varint(raw, pos)
    num, wire = key >> 3, key & 7
    if wire == 0:
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


def _proto_first(msg: dict[int, list], num: int, default=None):
  vals = msg.get(num) or []
  return vals[0] if vals else default


def open_conv_db(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> sqlite3.Connection | None:
  if not conv_id or not UUID_RE.match(conv_id):
    return None
  for d in harness_data_dirs(data_dir, config=config):
    path = os.path.join(d, "conversations", f"{conv_id}.db")
    if not os.path.isfile(path):
      continue
    uri = pathlib.Path(path).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)
  return None


def get_trajectory_parents(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> tuple[str, str]:
  """Returns (parent_conv_id, root_conv_id) from trajectory_metadata_blob."""
  con = open_conv_db(data_dir, conv_id, config=config)
  if con is None:
    return "", ""
  try:
    row = con.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
    if not row or not row[0]:
      return "", ""
    meta = _proto_fields(row[0])
    parent_raw = _proto_first(meta, META_PARENT, b"")
    root_raw = _proto_first(meta, META_ROOT, b"")
    parent = (
        parent_raw.decode("ascii", "replace")
        if isinstance(parent_raw, bytes)
        else ""
    )
    root = (
        root_raw.decode("ascii", "replace")
        if isinstance(root_raw, bytes)
        else ""
    )
    return parent, root
  except (sqlite3.Error, ValueError):
    return "", ""
  finally:
    con.close()


def is_subagent_db(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> bool:
  parent, root = get_trajectory_parents(data_dir, conv_id, config=config)
  return bool(parent) or bool(root and root != conv_id)


# --- Process table & FD inspection ---


_BOOT_TIME_UNSET = object()
_BOOT_TIME_CACHE = _BOOT_TIME_UNSET


def _linux_boot_time() -> int | None:
  """Reads host boot epoch (`btime`) from `/proc/stat` once."""
  global _BOOT_TIME_CACHE
  if _BOOT_TIME_CACHE is not _BOOT_TIME_UNSET:
    return _BOOT_TIME_CACHE  # type: ignore[return-value]
  val: int | None = None
  try:
    with open("/proc/stat", encoding="utf-8", errors="replace") as f:
      for line in f:
        if line.startswith("btime "):
          val = int(line.split()[1])
          break
  except (OSError, ValueError, IndexError):
    val = None
  _BOOT_TIME_CACHE = val
  return val


def _parse_ps_etime(etime: str) -> float | None:
  """Parses `ps` elapsed time `[[dd-]hh:]mm:ss` into seconds, or None."""
  s = (etime or "").strip()
  if not s:
    return None
  days = 0
  if "-" in s:
    day_part, s = s.split("-", 1)
    try:
      days = int(day_part)
    except ValueError:
      return None
  parts = s.split(":")
  try:
    if len(parts) == 2:
      hours, mins, secs = 0, int(parts[0]), float(parts[1])
    elif len(parts) == 3:
      hours, mins, secs = int(parts[0]), int(parts[1]), float(parts[2])
    else:
      return None
  except ValueError:
    return None
  return days * 86400 + hours * 3600 + mins * 60 + secs


def _populate_proc_cmdline_and_exe(info: dict) -> None:
  pid = info["pid"]
  proc_dir = f"/proc/{pid}"
  argv = None
  exe = ""
  try:
    with open(f"{proc_dir}/cmdline", "rb") as f:
      raw = f.read()
    if raw:
      argv = [
          p.decode("utf-8", "replace")
          for p in raw.rstrip(b"\0").split(b"\0")
      ]
  except OSError:
    pass
  try:
    exe = os.readlink(f"{proc_dir}/exe").removesuffix(" (deleted)")
  except OSError:
    pass
  if argv:
    info["argv"] = argv
    info["args_str"] = " ".join(argv)
  info["exe"] = exe


def _load_linux_proc_table(
    root_pids: set[int] | None = None,
) -> tuple[dict[int, dict], dict[int, list[int]]]:
  """Reads `/proc` directly without spawning a `ps` child process.

  When `root_pids` is provided, reads `/proc/<pid>/stat` to build the full
  parent-child tree, then reads `cmdline` and `exe` only for `root_pids`,
  their descendants, and this process's own lineage/descendants.
  """
  btime = _linux_boot_time()
  try:
    clk_tck = os.sysconf("SC_CLK_TCK")
  except (AttributeError, ValueError, OSError):
    clk_tck = 100

  procs: dict[int, dict] = {}
  children: dict[int, list[int]] = {}
  for entry in os.listdir("/proc"):
    if not entry.isdigit():
      continue
    pid = int(entry)
    proc_dir = f"/proc/{pid}"
    try:
      with open(f"{proc_dir}/stat", "rb") as f:
        stat_raw = f.read().decode("utf-8", "replace")
    except OSError:
      continue
    lparen = stat_raw.find("(")
    rparen = stat_raw.rfind(")")
    if lparen < 0 or rparen <= lparen:
      continue
    comm = stat_raw[lparen + 1 : rparen]
    after = stat_raw[rparen + 2 :].split()
    if len(after) < 5:
      continue
    stat_ch = after[0]
    try:
      ppid = int(after[1])
      tty_nr = int(after[4])
    except ValueError:
      continue
    start_epoch: float | None = None
    if btime is not None and clk_tck and len(after) > 19:
      try:
        start_epoch = btime + int(after[19]) / clk_tck
      except ValueError:
        start_epoch = None
    procs[pid] = {
        "pid": pid,
        "ppid": ppid,
        "stat": stat_ch,
        "tty": "",
        "tty_nr": tty_nr,
        "comm": comm,
        "argv": [comm],
        "args_str": comm,
        "exe": "",
        "start_epoch": start_epoch,
    }
    children.setdefault(ppid, []).append(pid)

  if root_pids is None:
    target_pids = procs.keys()
  else:
    target_pids = set()
    seeds = set(root_pids)
    my_pid = os.getpid()
    cur = my_pid
    while cur and cur not in target_pids:
      target_pids.add(cur)
      cur_info = procs.get(cur)
      cur = cur_info["ppid"] if cur_info else 0
    seeds.add(my_pid)
    for rpid in seeds:
      if rpid in procs:
        target_pids.add(rpid)
        target_pids.update(descendants_of(rpid, children))

  for pid in target_pids:
    info = procs.get(pid)
    if info is not None:
      _populate_proc_cmdline_and_exe(info)

  return procs, children


def load_process_table(
    config: HarnessConfig | None = None,
    root_pids: set[int] | None = None,
) -> tuple[dict[int, dict], dict[int, list[int]]]:
  """Returns ({pid: proc_dict}, {ppid: [child_pids]})."""
  global _PRELOADED_OPEN_FILES
  cfg = _get_config(config)
  if os.path.isdir("/proc"):
    _PRELOADED_OPEN_FILES = None
    return _load_linux_proc_table(root_pids=root_pids)

  now = time.time()
  res = subprocess.run(
      ["ps", "-ww", "-eo", "pid=,ppid=,stat=,tty=,etime=,args="],
      stdin=subprocess.DEVNULL,
      capture_output=True,
      text=True,
      check=True,
  )
  procs: dict[int, dict] = {}
  for line in res.stdout.splitlines():
    parts = line.strip().split(None, 5)
    if len(parts) < 6:
      continue
    try:
      pid = int(parts[0])
      ppid = int(parts[1])
    except ValueError:
      continue
    stat_ch, tty, etime_s, args_str = parts[2], parts[3], parts[4], parts[5]
    elapsed = _parse_ps_etime(etime_s)
    start_epoch = (now - elapsed) if elapsed is not None else None
    try:
      argv = shlex.split(args_str)
    except ValueError:
      argv = args_str.split()
    comm = os.path.basename(argv[0]) if argv else args_str
    procs[pid] = {
        "pid": pid,
        "ppid": ppid,
        "stat": stat_ch,
        "tty": tty,
        "tty_nr": 0,
        "comm": comm,
        "argv": argv,
        "args_str": args_str,
        "exe": "",
        "start_epoch": start_epoch,
    }
  cli_pids = [
      pid
      for pid, info in procs.items()
      if cli_proc_flavor(info, config=cfg)[0] is not None
  ]
  _PRELOADED_OPEN_FILES = _lsof_open_files_many(cli_pids)
  children: dict[int, list[int]] = {}
  for pid, info in procs.items():
    children.setdefault(info["ppid"], []).append(pid)
  return procs, children


def descendants_of(
    root_pid: int, children_map: Mapping[int, list[int]]
) -> list[int]:
  out: list[int] = []
  stack = list(children_map.get(root_pid, ()))
  seen = {root_pid}
  while stack:
    pid = stack.pop()
    if pid in seen:
      continue
    seen.add(pid)
    out.append(pid)
    stack.extend(children_map.get(pid, ()))
  return out


def is_self_script_proc(info: Mapping) -> bool:
  """True if `info` is an invocation of `code-harness` tmux helpers."""
  comm = info.get("comm") or ""
  if comm in ("auto-title", "auto-diff-p"):
    return True
  argv = info.get("argv") or ()
  if any(
      os.path.basename(tok) in ("auto-title", "auto-diff-pane")
      for tok in argv[:3]
  ):
    return True
  has_cmd = any(
      os.path.basename(tok) in ("code-harness", ".code-harness-wrapped")
      for tok in argv[:3]
  ) or (
      len(argv) >= 3
      and argv[1] == "-m"
      and argv[2] in ("code_harness", "code_harness.cli")
  )
  if has_cmd and any(
      sub in argv
      for sub in (
          "snapshot",
          "tmux-editor",
          "tmux-next",
          "tmux-notify",
          "tmux-status",
          "overview",
      )
  ):
    return True
  return False


def self_ignored_pids(
    procs: Mapping[int, dict], children_map: Mapping[int, list[int]]
) -> set[int]:
  """PIDs of this command, its ancestors, and its children so we ignore self."""
  my_pid = os.getpid()
  ignored = {my_pid}
  cur = my_pid
  while cur:
    info = procs.get(cur)
    if not info:
      break
    cur = info["ppid"]
    if cur and cur not in ignored:
      ignored.add(cur)
    else:
      break
  ignored.update(descendants_of(my_pid, children_map))
  for pid, info in procs.items():
    if is_self_script_proc(info):
      ignored.add(pid)
      ignored.update(descendants_of(pid, children_map))
  return ignored


def _is_excluded_for_flavor(
    arg0: str, exe: str, flavor_cfg: FlavorConfig
) -> bool:
  if not flavor_cfg.exclude_path_substrings:
    return False
  if is_excluded_path(arg0, flavor_cfg.exclude_path_substrings):
    return True
  if is_excluded_path(exe, flavor_cfg.exclude_path_substrings):
    return True
  return False


def cli_proc_flavor(
    info: Mapping,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, int]:
  """Returns (flavor_or_None, argv_start_idx) for a configured CLI process."""
  cfg = _get_config(config)
  env_map = os.environ if env is None else env
  argv = info.get("argv") or []
  exe = info.get("exe") or ""
  if not argv:
    return None, 1

  flavors = ordered_flavors(cfg)
  all_basenames = {
      b for flv in flavors for b in flv.match_basenames if b
  }
  all_path_substrings = tuple(
      sub for flv in flavors for sub in flv.match_path_substrings if sub
  )

  custom_bins: set[str] = set()
  if cfg.env_bin and env_map.get(cfg.env_bin):
    custom_bins.add(env_map[cfg.env_bin])
  for flv in flavors:
    if flv.env_bin and env_map.get(flv.env_bin):
      custom_bins.add(env_map[flv.env_bin])

  start_idx = 1
  arg0 = argv[0]
  base0 = os.path.basename(arg0)
  base_exe = os.path.basename(exe)

  # Handle `#!` wrapper scripts (e.g. `python3 /path/to/agy ...`).
  if (
      len(argv) >= 2
      and (base0.startswith("python") or base0 in SHELL_NAMES)
      and (
          os.path.basename(argv[1]) in all_basenames
          or argv[1] in custom_bins
      )
  ):
    arg0 = argv[1]
    base0 = os.path.basename(arg0)
    start_idx = 2

  active_flavors = [
      flv for flv in flavors if not _is_excluded_for_flavor(arg0, exe, flv)
  ]
  if not active_flavors:
    return None, start_idx

  app_data = extract_flag_value(argv[start_idx - 1 :], "--app_data_dir")
  if app_data:
    app_base = os.path.basename(app_data.rstrip("/"))
    is_cli_like = (
        base0 in ("cli", "antigravity")
        or base0 in all_basenames
        or base_exe in all_basenames
        or any(sub in exe or sub in arg0 for sub in all_path_substrings)
    )
    if is_cli_like:
      for flv in active_flavors:
        if app_base in flv.match_app_data_basenames:
          return flv.name, start_idx

  # Prefer longest matching path substring so more specific patterns win
  # over shorter prefix substrings across flavors.
  best_sub_len = 0
  best_sub_flavor: str | None = None
  for flv in active_flavors:
    for sub in flv.match_path_substrings:
      if sub and (sub in exe or sub in arg0) and len(sub) > best_sub_len:
        best_sub_len = len(sub)
        best_sub_flavor = flv.name
  if best_sub_flavor is not None:
    return best_sub_flavor, start_idx

  for flv in active_flavors:
    if base0 in flv.match_basenames or base_exe in flv.match_basenames:
      return flv.name, start_idx

  for flv in active_flavors:
    if flv.env_bin:
      fbin = env_map.get(flv.env_bin) or ""
      if fbin and (arg0 == fbin or exe == fbin):
        return flv.name, start_idx

  if cfg.env_bin:
    gbin = env_map.get(cfg.env_bin) or ""
    if gbin and (arg0 == gbin or exe == gbin):
      gbase = os.path.basename(gbin).lower()
      for flv in active_flavors:
        if flv.name.lower() in gbase or any(
            b.lower() in gbase for b in flv.match_basenames
        ):
          return flv.name, start_idx
      return active_flavors[0].name, start_idx

  return None, start_idx


def is_cli_proc(
    info: Mapping,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, bool]:
  """Returns (is_cli, is_print_mode) for a process entry."""
  cfg = _get_config(config)
  flavor, start_idx = cli_proc_flavor(info, config=cfg, env=env)
  if flavor is None:
    return False, False

  argv = info.get("argv") or []
  is_print = False
  has_hub = False
  has_remote_control = False
  nonzero_http_port = False
  i = start_idx
  first_pos = None
  while i < len(argv):
    tok = argv[i]
    if tok == "--":
      break
    if not tok.startswith("-") or tok == "-":
      first_pos = tok
      break

    name_part, has_eq, val_part = tok.partition("=")
    canon = (
        name_part
        if name_part in ("-i", "-p")
        else "--" + name_part.lstrip("-")
    )

    if canon in (
        "--bg-updater",
        "--stamp",
        "--h",
        "--help",
        "--v",
        "--version",
        "--n",
        "--new-window",
        "--r",
        "--reuse-window",
        "--w",
        "--wait",
        "--list-extensions",
    ):
      if not has_eq or val_part.lower() not in ("0", "false"):
        return False, False

    if (has_eq and canon == "--type") or canon.startswith((
        "--install-extension",
        "--uninstall-extension",
        "--user-data-dir",
        "--extensions-dir",
    )):
      return False, False

    if canon == "--hub":
      if not has_eq or val_part.lower() not in ("0", "false"):
        has_hub = True

    if canon == "--remote-control":
      if not has_eq or val_part.lower() not in ("0", "false"):
        has_remote_control = True

    if canon in ("--hub-port", "--http_server_port"):
      port_str = (
          val_part if has_eq else (argv[i + 1] if i + 1 < len(argv) else "")
      )
      try:
        if port_str and int(port_str) != 0:
          nonzero_http_port = True
      except ValueError:
        if port_str:
          nonzero_http_port = True

    if canon in ("-p", "--print", "--prompt"):
      is_print = True

    if not has_eq and canon in cfg.cli_takes_value:
      i += 2
      continue
    i += 1

  if has_hub or (has_remote_control and nonzero_http_port):
    return False, False
  if first_pos in cfg.non_tui_subcommands:
    return False, False
  if first_pos is not None:
    # Unknown subcommand or invalid positional arg.
    return False, False
  return True, is_print


_PRELOADED_OPEN_FILES: dict[int, dict[str, str]] | None = None


def _find_lsof_bin() -> str:
  return shutil.which("lsof") or (
      "/usr/sbin/lsof" if os.access("/usr/sbin/lsof", os.X_OK) else "lsof"
  )


def _lsof_open_files_many(pids: list[int]) -> dict[int, dict[str, str]]:
  """Returns `{pid: {fd_str: target_path}}` via batched `lsof`, or `{}`."""
  unique_pids = sorted(set(pids))
  if not unique_pids:
    return {}
  by_pid: dict[int, dict[str, str]] = {p: {} for p in unique_pids}
  try:
    res = subprocess.run(
        [
            _find_lsof_bin(),
            "-n",
            "-P",
            "-l",
            "-a",
            "-d",
            "0-999",
            "-Fn",
            "-p",
            ",".join(str(p) for p in unique_pids),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
  except OSError:
    return {}

  cur_pid: int | None = unique_pids[0] if len(unique_pids) == 1 else None
  cur_fd: str | None = None
  idx = 0
  for line in res.stdout.splitlines():
    if not line:
      continue
    tag, val = line[0], line[1:]
    if tag == "p":
      try:
        cur_pid = int(val)
        by_pid.setdefault(cur_pid, {})
      except ValueError:
        cur_pid = None
      cur_fd = None
      idx = 0
    elif tag == "f":
      cur_fd = val
    elif tag == "n" and cur_pid is not None:
      idx += 1
      by_pid[cur_pid][cur_fd or str(idx)] = val
      cur_fd = None
  return by_pid


def list_open_files(pid: int) -> dict[str, str]:
  """Returns {fd_str: target_path} for `pid`."""
  fd_dir = f"/proc/{pid}/fd"
  if os.path.isdir(fd_dir):
    targets: dict[str, str] = {}
    # Will raise PermissionError if unreadable so we never guess blindly.
    for entry in os.listdir(fd_dir):
      try:
        target = os.readlink(f"{fd_dir}/{entry}").removesuffix(" (deleted)")
        targets[entry] = target
      except OSError:
        continue
    return targets

  # Fallback for macOS / non-/proc environments.
  if _PRELOADED_OPEN_FILES is not None and pid in _PRELOADED_OPEN_FILES:
    return dict(_PRELOADED_OPEN_FILES[pid])
  return _lsof_open_files_many([pid]).get(pid, {})


def _pane_state_path(pane_id: str) -> str:
  runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
  return os.path.join(runtime_dir, f"code-harness-pane-{pane_id}.json")


def extract_flag_value(argv: list[str], flag: str) -> str | None:
  name = flag.lstrip("-")
  forms = (f"--{name}", f"-{name}")
  prefixes = tuple(f + "=" for f in forms)
  for i, arg in enumerate(argv[1:], start=1):
    if arg == "--":
      break
    for pref in prefixes:
      if arg.startswith(pref):
        return arg[len(pref) :]
    if arg in forms and i + 1 < len(argv):
      return argv[i + 1]
  return None


def resolve_cli_data_dir(
    cli_info: Mapping,
    data_dir: str | None = None,
    fds: Mapping[str, str] | None = None,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  cfg = _get_config(config)
  env_map = os.environ if env is None else env
  defaults = default_app_data_dirs(cfg)
  if data_dir and data_dir not in defaults:
    return data_dir
  env_dir = env_map.get(cfg.env_data_dir) if cfg.env_data_dir else None
  if env_dir:
    return data_dir or os.path.expanduser(env_dir)

  flavor, start_idx = cli_proc_flavor(cli_info, config=cfg, env=env_map)
  argv = cli_info.get("argv") or []
  flag_argv = argv[max(0, start_idx - 1) :]
  flag_gemini = extract_flag_value(flag_argv, "--gemini_dir")
  flag_app = extract_flag_value(flag_argv, "--app_data_dir")

  if flag_gemini:
    expanded_g = os.path.expanduser(flag_gemini)
    gemini_base = (
        expanded_g
        if os.path.isabs(expanded_g)
        else os.path.join(os.path.expanduser("~"), expanded_g)
    )
  else:
    gemini_base = os.path.expanduser("~/.gemini")

  if flag_app:
    return os.path.join(gemini_base, os.path.expanduser(flag_app))

  if fds is None and cli_info.get("pid"):
    try:
      fds = list_open_files(cli_info["pid"])
    except OSError:
      fds = {}

  if fds:
    for pattern in (LOCK_RE, DB_RE):
      for target in fds.values():
        m = pattern.search(target)
        if m:
          return target[: m.start()]

  flavor_cfg = cfg.flavors.get(flavor or cfg.default_flavor)
  app_dir = (
      flavor_cfg.data_dir_name
      if flavor_cfg and flavor_cfg.data_dir_name
      else "antigravity-cli"
  )
  return os.path.join(gemini_base, app_dir)


def resolve_cli_conversation_and_dir(
    cli_info: Mapping,
    pane_id: str,
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
) -> tuple[str | None, set[str], str]:
  """Returns (conv_id_or_None, set_of_open_db_ids, cli_data_dir)."""
  cfg = _get_config(config)
  pid = cli_info["pid"]
  fds = list_open_files(pid)
  cli_dir = resolve_cli_data_dir(
      cli_info, data_dir=data_dir, fds=fds, config=cfg
  )

  open_dbs: set[str] = set()
  lock_ids: list[str] = []
  for target in fds.values():
    m_lock = LOCK_RE.search(target)
    if m_lock:
      cid = m_lock.group(1)
      if cid not in lock_ids:
        lock_ids.append(cid)
    m_db = DB_RE.search(target)
    if m_db:
      open_dbs.add(m_db.group(1))

  # Optional statusLine/title JSON state file if user configured one.
  pane_state_path = _pane_state_path(pane_id)
  if os.path.isfile(pane_state_path):
    try:
      with open(pane_state_path, encoding="utf-8") as f:
        st = json.load(f)
      cid = st.get("conversation_id") or ""
      if UUID_RE.match(cid) and (cid in lock_ids or cid in open_dbs):
        return cid, open_dbs, cli_dir
    except (OSError, ValueError):
      pass

  # Primary zero-config source: active presence lock FD.
  if lock_ids:
    cid = lock_ids[-1]
    if len(open_dbs) > 1:
      _, root_cid = get_trajectory_parents(cli_dir, cid, config=cfg)
      if root_cid and UUID_RE.match(root_cid):
        return root_cid, open_dbs, cli_dir
    return cid, open_dbs, cli_dir

  # Contention fallback: check CLI stderr log and open root conversation DBs.
  stderr_log = fds.get("2") or ""
  if stderr_log.endswith(".log") and os.path.isfile(stderr_log):
    try:
      with open(stderr_log, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 65536))
        tail = f.read().decode("utf-8", "replace")
      matches = RESUME_LOG_RE.findall(tail)
      if matches:
        return matches[-1], open_dbs, cli_dir
    except OSError:
      pass

  root_dbs = [c for c in open_dbs if not is_subagent_db(cli_dir, c, config=cfg)]
  if len(root_dbs) == 1:
    return root_dbs[0], open_dbs, cli_dir
  if len(root_dbs) > 1:
    # Pick the DB whose WAL or main file was touched most recently.
    def db_mtime(cid: str) -> float:
      mtimes = []
      for d in harness_data_dirs(cli_dir, config=cfg):
        base = os.path.join(d, "conversations", f"{cid}.db")
        for p in (base + "-wal", base):
          try:
            mtimes.append(os.path.getmtime(p))
          except OSError:
            pass
      return max(mtimes) if mtimes else 0.0

    root_dbs.sort(key=db_mtime, reverse=True)
    return root_dbs[0], open_dbs, cli_dir

  flag_cid = extract_flag_value(cli_info["argv"], "--conversation")
  if flag_cid and UUID_RE.match(flag_cid):
    return flag_cid, open_dbs, cli_dir

  return None, open_dbs, cli_dir


def resolve_cli_conversation(
    cli_info: Mapping,
    pane_id: str,
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
) -> tuple[str | None, set[str]]:
  """Returns (conv_id_or_None, set_of_open_db_ids)."""
  conv_id, open_dbs, _ = resolve_cli_conversation_and_dir(
      cli_info, pane_id, data_dir=data_dir, config=config
  )
  return conv_id, open_dbs


# --- Conversation state & title inspection ---


def load_mcp_commands(
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
) -> set[tuple[str, str | None]]:
  """Returns set of (basename, first_arg_or_None) for configured MCP servers."""
  sigs: set[tuple[str, str | None]] = set()
  candidates = [
      os.path.join(d, "mcp_config.json")
      for d in harness_data_dirs(data_dir, config=config)
  ]
  candidates.extend([
      os.path.expanduser("~/.gemini/mcp_config.json"),
      os.path.expanduser("~/.gemini/config/mcp_config.json"),
  ])
  seen: set[str] = set()
  for path in candidates:
    if path in seen or not os.path.isfile(path):
      continue
    seen.add(path)
    try:
      with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
      for srv in (cfg.get("mcpServers") or {}).values():
        cmd = srv.get("command") or ""
        args = srv.get("args") or []
        if cmd:
          sigs.add((os.path.basename(cmd), args[0] if args else None))
    except (OSError, ValueError):
      continue
  return sigs


def is_ignored_cli_child(
    proc_info: Mapping, mcp_sigs: set[tuple[str, str | None]]
) -> bool:
  if proc_info["stat"].startswith("Z"):
    return True
  argv = proc_info["argv"] or []
  if not argv:
    return True
  if "--bg-updater" in argv[1:]:
    return True
  base = os.path.basename(argv[0])
  if base.startswith("envelope_") or any(
      a.startswith("--envelope_") for a in argv[1:]
  ):
    return True
  arg1 = argv[1] if len(argv) > 1 else None
  if "mcp-server" in argv[1:] or "mcp_server" in argv[1:]:
    return True
  if (base, arg1) in mcp_sigs or (base, None) in mcp_sigs:
    return True
  return False


def active_cli_child_processes(
    cli_pid: int,
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    mcp_sigs: set[tuple[str, str | None]],
    self_ignored: set[int],
) -> list[dict]:
  """Returns non-MCP, non-zombie child processes under `cli_pid`."""
  # Prune entire subtrees rooted at an MCP server so grandchildren of MCP
  # servers (e.g. dart analysis workers) are also ignored.
  active: list[dict] = []
  stack = list(children_map.get(cli_pid, ()))
  seen = {cli_pid}
  while stack:
    pid = stack.pop()
    if pid in seen:
      continue
    seen.add(pid)
    info = procs.get(pid)
    if not info:
      continue
    if is_ignored_cli_child(info, mcp_sigs):
      continue
    if pid not in self_ignored:
      active.append(info)
    stack.extend(children_map.get(pid, ()))
  return active


def find_pane_cli(
    pane_pid: int,
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    config: HarnessConfig | None = None,
) -> tuple[dict | None, bool]:
  """Finds topmost coding-agent CLI in a pane: (proc_or_None, is_print)."""
  root_info = procs.get(pane_pid)
  if root_info:
    ok, is_pr = is_cli_proc(root_info, config=config)
    if ok:
      return root_info, is_pr
  # Breadth/top-down check so we pick the parent CLI, not any child CLI.
  queue = list(children_map.get(pane_pid, ()))
  visited = {pane_pid}
  while queue:
    cpid = queue.pop(0)
    if cpid in visited:
      continue
    visited.add(cpid)
    cinfo = procs.get(cpid)
    if not cinfo:
      continue
    ok, is_pr = is_cli_proc(cinfo, config=config)
    if ok:
      return cinfo, is_pr
    queue.extend(children_map.get(cpid, ()))
  return None, False


def _transcript_path(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> str:
  dirs = harness_data_dirs(data_dir, config=config)
  for d in dirs:
    path = os.path.join(
        d,
        "brain",
        conv_id,
        ".system_generated",
        "logs",
        "transcript.jsonl",
    )
    if os.path.isfile(path):
      return path
  return os.path.join(
      dirs[0],
      "brain",
      conv_id,
      ".system_generated",
      "logs",
      "transcript.jsonl",
  )


def read_transcript_entries(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> list[dict]:
  path = _transcript_path(data_dir, conv_id, config=config)
  if not os.path.isfile(path):
    return []
  entries: list[dict] = []
  try:
    with open(path, encoding="utf-8", errors="replace") as f:
      for line in f:
        line = line.strip()
        if not line:
          continue
        try:
          entries.append(json.loads(line))
        except ValueError:
          continue
  except OSError:
    return []
  return entries


def read_last_transcript_entries(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> list[dict]:
  """Reads the tail of transcript.jsonl needed to inspect trailing steps."""
  path = _transcript_path(data_dir, conv_id, config=config)
  if not os.path.isfile(path):
    return []
  try:
    size = os.path.getsize(path)
  except OSError:
    return []
  if size == 0:
    return []

  chunk = 65536
  while True:
    offset = max(0, size - chunk)
    try:
      with open(path, "rb") as f:
        if offset > 0:
          f.seek(offset)
        raw = f.read()
    except OSError:
      return []
    lines = raw.splitlines()
    if offset > 0 and lines:
      lines = lines[1:]
    entries: list[dict] = []
    for raw_line in lines:
      s = raw_line.strip()
      if not s:
        continue
      try:
        entries.append(json.loads(s.decode("utf-8", "replace")))
      except ValueError:
        continue
    if offset == 0 or any(not _is_trailing_sdk_hook_entry(e) for e in entries):
      return entries
    chunk *= 4


def _parse_iso_dt(iso_str: str) -> datetime.datetime | None:
  if not iso_str:
    return None
  s = iso_str.strip()
  if s.endswith("Z"):
    s = s[:-1] + "+00:00"
  s = ISO_FRAC_RE.sub(lambda m: f".{m.group(1)[:6]:<06}", s, count=1)
  try:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt
  except ValueError:
    return None


def _iso_gt(ts_a: str, ts_b: str) -> bool:
  dt_a = _parse_iso_dt(ts_a)
  dt_b = _parse_iso_dt(ts_b)
  if dt_a is not None and dt_b is not None:
    return dt_a > dt_b
  return bool(ts_a and ts_b and ts_a > ts_b)


def _newer_iso_ts(ts_a: str, ts_b: str) -> str:
  """Returns whichever ISO-8601 timestamp is newer, or the non-empty one."""
  if not ts_a:
    return ts_b or ""
  if not ts_b:
    return ts_a
  dt_a = _parse_iso_dt(ts_a)
  dt_b = _parse_iso_dt(ts_b)
  if dt_a is not None and dt_b is not None:
    return ts_b if dt_b >= dt_a else ts_a
  return ts_b or ts_a


def _step_metadata_iso_ts(meta_blob: bytes | None) -> str:
  """Extracts step metadata created_at (field 1) as ISO-8601 UTC."""
  if not meta_blob:
    return ""
  try:
    meta = _proto_fields(meta_blob)
    ts_raw = _proto_first(meta, 1)
    if not isinstance(ts_raw, bytes):
      return ""
    ts_msg = _proto_fields(ts_raw)
    secs = _proto_first(ts_msg, 1, 0) or 0
    if not isinstance(secs, int) or secs <= 0:
      return ""
    nanos = _proto_first(ts_msg, 2, 0) or 0
    dt = datetime.datetime.fromtimestamp(
        secs + nanos / 1e9, datetime.timezone.utc
    )
    return dt.isoformat().replace("+00:00", "Z")
  except (ValueError, OSError, OverflowError):
    return ""


def _waiting_step_ts(
    data_dir: str | None,
    conv_id: str,
    md_blob: bytes | None,
    config: HarnessConfig | None = None,
) -> str:
  sql_ts = _step_metadata_iso_ts(md_blob)
  _, tx_ts = _trailing_transcript_entry_and_ts(
      read_last_transcript_entries(data_dir, conv_id, config=config)
  )
  return _newer_iso_ts(sql_ts, tx_ts)


def _is_recent_iso_timestamp(iso_str: str, max_age_sec: float = 60) -> bool:
  ts = _parse_iso_dt(iso_str)
  if ts is None:
    return False
  age = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
  return 0 <= age < max_age_sec


def _is_trailing_sdk_hook_entry(entry: Mapping) -> bool:
  """True for PostInvocation hook records appended after PLANNER_RESPONSE."""
  return (
      entry.get("source") == "SYSTEM_SDK"
      and entry.get("type") == "SYSTEM_MESSAGE"
      and not (entry.get("content") or "").strip()
  )


def _is_daemon_task(task_blob: bytes | None) -> bool:
  """True when protobuf TaskDetails sets `is_daemon` (field 6)."""
  if not task_blob:
    return False
  try:
    msg = _proto_fields(task_blob)
    return bool(_proto_first(msg, TASK_DETAILS_IS_DAEMON, 0))
  except ValueError:
    return False


def _task_label(task_blob: bytes | None) -> str:
  """Returns title (7) or description (4) from protobuf TaskDetails."""
  if not task_blob:
    return ""
  try:
    msg = _proto_fields(task_blob)
  except ValueError:
    return ""
  for field in (TASK_DETAILS_TITLE, TASK_DETAILS_DESCRIPTION):
    raw = _proto_first(msg, field)
    if isinstance(raw, bytes) and raw:
      text = " ".join(raw.decode("utf-8", "replace").split())
      if text:
        return text
  return ""


def _proto_submsgs(msgs: list[dict], field: int) -> list[dict]:
  out: list[dict] = []
  for msg in msgs:
    for raw in msg.get(field) or ():
      if not isinstance(raw, bytes):
        continue
      try:
        out.append(_proto_fields(raw))
      except ValueError:
        continue
  return out


def _workflow_agent_ids_from_step(step_payload: bytes | None) -> list[str]:
  """Extracts child AgentRef conversationIds from a generic step payload."""
  if not step_payload:
    return []
  try:
    msgs = [_proto_fields(step_payload)]
  except ValueError:
    return []
  for field in (
      STEP_GENERIC,
      GENERIC_RESULT,
      GENERIC_STEP_RENDER_INFO,
      RENDER_INFO_CONTENT_BLOCKS,
      CONTENT_BLOCK_AGENT,
  ):
    msgs = _proto_submsgs(msgs, field)
    if not msgs:
      return []
  ids: list[str] = []
  seen: set[str] = set()
  for ag in msgs:
    for raw in ag.get(AGENT_REF_CONVERSATION_ID) or ():
      if not isinstance(raw, bytes):
        continue
      cid = raw.decode("ascii", "replace")
      if UUID_RE.match(cid) and cid not in seen:
        seen.add(cid)
        ids.append(cid)
  return ids


def _brain_dirs_for_conv(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> list[str]:
  """Returns existing `brain/<conv_id>` dirs across app data directories."""
  if not conv_id or not UUID_RE.match(conv_id):
    return []
  out: list[str] = []
  for d in harness_data_dirs(data_dir, config=config):
    bdir = os.path.join(d, "brain", conv_id)
    if os.path.isdir(bdir):
      out.append(bdir)
  return out


def has_wakeable_undelivered_message(
    data_dir: str | None,
    conv_id: str,
    min_epoch: float | None = None,
    config: HarnessConfig | None = None,
) -> bool:
  """True if `conv_id` has a non-low-priority undelivered message."""
  for bdir in _brain_dirs_for_conv(data_dir, conv_id, config=config):
    msg_root = os.path.join(bdir, ".system_generated", "messages")
    undeliv_dir = os.path.join(msg_root, "undelivered")
    if not os.path.isdir(undeliv_dir):
      continue
    try:
      names = os.listdir(undeliv_dir)
    except OSError:
      continue
    for name in names:
      marker = os.path.join(undeliv_dir, name)
      try:
        st = os.stat(marker)
      except OSError:
        continue
      if not stat.S_ISREG(st.st_mode):
        continue
      if min_epoch is not None and st.st_mtime < min_epoch:
        continue
      meta_path = os.path.join(msg_root, f"{name}.json")
      try:
        with open(meta_path, encoding="utf-8", errors="replace") as f:
          data = json.load(f)
        if (
            isinstance(data, dict)
            and data.get("priority") == "MESSAGE_PRIORITY_LOW"
        ):
          continue
      except (OSError, ValueError):
        pass
      return True
  return False


def _trailing_transcript_entry_and_ts(
    entries: list[dict],
) -> tuple[dict | None, str]:
  if not entries:
    return None, ""
  eff_idx = len(entries) - 1
  while eff_idx > 0 and _is_trailing_sdk_hook_entry(entries[eff_idx]):
    eff_idx -= 1
  last = entries[eff_idx]
  last_ts = last.get("created_at") or entries[-1].get("created_at") or ""
  return last, last_ts


def _generic_output_body(entry: Mapping) -> str:
  """Returns generic step `content` with leading timestamp headers stripped."""
  content = entry.get("content") or ""
  return GENERIC_TS_HEADER_RE.sub("", content, count=1)


def _is_parked_on_swarm_wait(
    entries: list[dict], has_db: bool = True
) -> bool:
  """True when trailing DONE generic steps come from a parking swarm_wait."""
  i = len(entries) - 1
  while i >= 0 and _is_trailing_sdk_hook_entry(entries[i]):
    i -= 1
  if i < 0:
    return False

  generics: list[dict] = []
  while i >= 0:
    e = entries[i]
    if _is_trailing_sdk_hook_entry(e):
      i -= 1
      continue
    if e.get("type") == "GENERIC" and e.get("status") == "DONE":
      generics.append(e)
      i -= 1
      continue
    break

  if not generics:
    return False

  if i >= 0:
    prev = entries[i]
    if prev.get("type") != "PLANNER_RESPONSE" or prev.get("status") != "DONE":
      return False
    tool_calls = prev.get("tool_calls") or []
    if not any(
        isinstance(tc, dict) and tc.get("name") == "swarm_wait"
        for tc in tool_calls
    ):
      return False
    if not has_db and len(generics) < len(tool_calls):
      return False
    return any(
        _generic_output_body(g).lstrip().startswith("Waiting for ")
        for g in generics
    )

  return bool(
      has_db
      and any(
          SWARM_WAIT_OUTPUT_RE.match(_generic_output_body(g).strip())
          for g in generics
      )
  )


def classify_waiting_step(
    step_type: int, step_payload: bytes | None
) -> tuple[str, str]:
  """Classifies a STATUS_WAITING step into (CLI_STATE_*, reason)."""
  if step_payload:
    try:
      step_msg = _proto_fields(step_payload)
      req_raw = _proto_first(step_msg, STEP_REQUESTED_INTERACTION)
      if req_raw:
        req = _proto_fields(req_raw)
        if INTERACTION_ASK_QUESTION in req:
          return CLI_STATE_QUESTION, "waiting on user question"
        if INTERACTION_ELICITATION in req:
          return CLI_STATE_PERMISSION, "waiting on MCP elicitation"
        if INTERACTION_PERMISSION in req:
          return CLI_STATE_PERMISSION, "waiting on permission check"
        if req:
          return CLI_STATE_PERMISSION, "waiting on permission check"
    except ValueError:
      pass
  if step_type == STEP_TYPE_ASK_QUESTION:
    return CLI_STATE_QUESTION, "waiting on user question"
  return CLI_STATE_PERMISSION, "waiting on permission check"


def _meta_blob_requests_feedback(am_vals: list | None) -> bool | None:
  """Returns True/False when ArtifactMetadata is present, or None if omitted."""
  if not am_vals:
    return None
  am_raw = am_vals[0]
  if not isinstance(am_raw, bytes):
    return False
  try:
    am = _proto_fields(am_raw)
  except ValueError:
    return False
  return bool(_proto_first(am, ARTIFACT_METADATA_REQUEST_FEEDBACK, 0))


def _sidecar_requests_feedback(path: str) -> bool:
  """Reads `<path>.metadata.json` sidecar for `requestFeedback == True`."""
  if not path:
    return False
  try:
    with open(f"{path}.metadata.json", encoding="utf-8", errors="replace") as f:
      data = json.load(f)
    return isinstance(data, dict) and data.get("requestFeedback") is True
  except (OSError, ValueError):
    return False


def _code_action_artifact_path(ca: Mapping[int, list]) -> str:
  """Extracts optional target file path from `CortexStepCodeAction`."""
  raw = _proto_first(ca, CODE_ACTION_SPEC)
  if not isinstance(raw, bytes) or not raw:
    return ""
  try:
    spec = _proto_fields(raw)
  except ValueError:
    text = raw.decode("utf-8", "replace").strip()
    if text and "\x00" not in text and ("/" in text or text.endswith(".md")):
      return text
    return ""
  for sf_num in ACTION_SPEC_SUBFIELDS:
    sub_raw = _proto_first(spec, sf_num)
    if not isinstance(sub_raw, bytes) or not sub_raw:
      continue
    try:
      sub = _proto_fields(sub_raw)
    except ValueError:
      continue
    for pf_num in ACTION_PATH_FIELDS:
      p_raw = _proto_first(sub, pf_num)
      if isinstance(p_raw, bytes) and p_raw:
        p_text = p_raw.decode("utf-8", "replace").strip()
        if p_text and "\x00" not in p_text:
          return p_text
  return ""


def _artifact_edit_feedback(
    step_type: int, step_payload: bytes | None
) -> tuple[str | None, bool]:
  """Returns `(artifact_key_or_None, requests_feedback_bool)` for an edit."""
  if not step_payload:
    return None, False
  try:
    sf = _proto_fields(step_payload)
  except ValueError:
    return None, False

  if step_type == STEP_TYPE_GENERIC:
    gen_raw = _proto_first(sf, STEP_GENERIC)
    if not isinstance(gen_raw, bytes):
      return None, False
    try:
      gen = _proto_fields(gen_raw)
      res_raw = _proto_first(gen, GENERIC_RESULT)
      if not isinstance(res_raw, bytes):
        return None, False
      gres = _proto_fields(res_raw)
      any_raw = _proto_first(gres, GENERIC_RESULT_PAYLOAD)
      if not isinstance(any_raw, bytes):
        return None, False
      any_msg = _proto_fields(any_raw)
      type_url = _proto_first(any_msg, ANY_TYPE_URL)
      if not isinstance(type_url, bytes) or not type_url.endswith(b"FileEdit"):
        return None, False
      val_raw = _proto_first(any_msg, ANY_VALUE)
      if not isinstance(val_raw, bytes):
        return None, False
      fe = _proto_fields(val_raw)
    except ValueError:
      return None, False

    if not _proto_first(fe, FILE_EDIT_IS_ARTIFACT_FILE, 0):
      return None, False
    path = ""
    path_raw = _proto_first(fe, FILE_EDIT_PATH)
    if isinstance(path_raw, bytes) and path_raw:
      path = path_raw.decode("utf-8", "replace")
      if os.path.basename(path) in ("walkthrough.md", "task.md"):
        return None, False

    fb = _meta_blob_requests_feedback(fe.get(FILE_EDIT_ARTIFACT_METADATA))
    if fb is None and path:
      fb = _sidecar_requests_feedback(path)
    return (path or "__unnamed_artifact__"), bool(fb)

  if step_type == STEP_TYPE_CODE_ACTION:
    ca_raw = _proto_first(sf, STEP_CODE_ACTION)
    if not isinstance(ca_raw, bytes):
      return None, False
    try:
      ca = _proto_fields(ca_raw)
    except ValueError:
      return None, False
    if not _proto_first(ca, CODE_ACTION_IS_ARTIFACT_FILE, 0):
      return None, False
    path = _code_action_artifact_path(ca)
    if path and os.path.basename(path) in ("walkthrough.md", "task.md"):
      return None, False
    fb = _meta_blob_requests_feedback(ca.get(CODE_ACTION_ARTIFACT_METADATA))
    if fb is None and path:
      fb = _sidecar_requests_feedback(path)
    return (path or "__code_action__"), bool(fb)

  return None, False


def _step_requests_plan_approval(
    step_type: int, step_payload: bytes | None
) -> bool:
  """True when a completed edit step wrote an artifact requesting feedback."""
  _, req_fb = _artifact_edit_feedback(step_type, step_payload)
  return bool(req_fb)


def _inspect_trajectory_state(
    data_dir: str | None,
    conv_id: str,
    is_child: bool = False,
    config: HarnessConfig | None = None,
) -> tuple[str, str, str, bool, bool, tuple[str, ...]]:
  """Returns (state, reason, last_ts, has_db, was_canceled, wf_ids)."""
  has_db = False
  eff_sql = None
  pending_plan = False
  plan_step_ts = ""
  wf_ids: list[str] = []
  wf_seen: set[str] = set()
  con = open_conv_db(data_dir, conv_id, config=config)
  if con is not None:
    has_db = True
    try:
      latest = con.execute(
          "SELECT idx FROM steps ORDER BY idx DESC LIMIT 1"
      ).fetchone()
      if latest is None:
        return CLI_STATE_SETTLED, "empty conversation", "", True, False, ()
      max_idx = latest[0]
      ui_row = con.execute(
          "SELECT MAX(idx) FROM steps WHERE step_type = ?",
          (STEP_TYPE_USER_INPUT,),
      ).fetchone()
      ui_val = ui_row[0] if (ui_row and ui_row[0] is not None) else None
      ui_idx = ui_val if ui_val is not None else 0
      last_ui_idx = ui_val if ui_val is not None else -1
      min_idx = min(ui_idx, max(0, max_idx - 25))
      ckpt_row = con.execute(
          "SELECT COALESCE(MAX(idx), 0) FROM steps "
          "WHERE step_type = ? AND status = ?",
          (STEP_TYPE_CHECKPOINT, STATUS_DONE),
      ).fetchone()
      ckpt_idx = ckpt_row[0] if (ckpt_row and ckpt_row[0] is not None) else 0
      lower_idx = min(min_idx, ckpt_idx)
      cols = {r[1] for r in con.execute("PRAGMA table_info(steps)").fetchall()}
      td_col = (
          "task_details" if "task_details" in cols else "NULL AS task_details"
      )
      pl_col = (
          "step_payload" if "step_payload" in cols else "NULL AS step_payload"
      )
      md_col = "metadata" if "metadata" in cols else "NULL AS metadata"
      raw_rows = con.execute(
          f"SELECT idx, step_type, status, {td_col}, {pl_col}, {md_col} "
          "FROM steps WHERE idx >= ? AND status IN (?, ?, ?, ?, ?) "
          "ORDER BY idx ASC",
          (
              lower_idx,
              STATUS_PENDING,
              STATUS_RUNNING,
              STATUS_GENERATING,
              STATUS_WAITING,
              STATUS_QUEUED,
          ),
      ).fetchall()
      kept = []
      for row_idx, st_type, st_val, task_blob, payload, md_blob in raw_rows:
        keep = False
        if st_val == STATUS_WAITING:
          keep = row_idx >= ckpt_idx
        elif task_blob:
          keep = row_idx >= ckpt_idx and not _is_daemon_task(task_blob)
        else:
          keep = row_idx >= min_idx
        if not keep:
          continue
        kept.append((row_idx, st_type, st_val, task_blob, payload, md_blob))
        if st_type == STEP_TYPE_GENERIC and payload:
          for cid in _workflow_agent_ids_from_step(payload):
            if cid not in wf_seen:
              wf_seen.add(cid)
              wf_ids.append(cid)
      if kept:
        first_question = None
        for _, st_type, st_val, _, payload, md_blob in kept:
          if st_val == STATUS_WAITING:
            w_state, w_reason = classify_waiting_step(st_type, payload)
            if w_state == CLI_STATE_PERMISSION:
              return (
                  CLI_STATE_PERMISSION,
                  w_reason,
                  _waiting_step_ts(data_dir, conv_id, md_blob, config=config),
                  True,
                  False,
                  tuple(wf_ids),
              )
            if first_question is None:
              first_question = (w_state, w_reason, md_blob)
        if first_question is not None:
          w_state, w_reason, md_blob = first_question
          return (
              w_state,
              w_reason,
              _waiting_step_ts(data_dir, conv_id, md_blob, config=config),
              True,
              False,
              tuple(wf_ids),
          )
        if kept[-1][2] == STATUS_GENERATING:
          return (
              CLI_STATE_WORKING,
              "model generating response",
              "",
              True,
              False,
              tuple(wf_ids),
          )
        if all(row[3] for row in kept):
          label = ""
          for row in kept:
            label = _task_label(row[3])
            if label:
              break
          msg = (
              f"background task active ({label})"
              if label
              else "background task active"
          )
          return CLI_STATE_WORKING, msg, "", True, False, tuple(wf_ids)
        return (
            CLI_STATE_WORKING,
            "step currently running",
            "",
            True,
            False,
            tuple(wf_ids),
        )

      tail_rows = con.execute(
          "SELECT step_type, status FROM steps ORDER BY idx DESC LIMIT 10"
      ).fetchall()
      if tail_rows:
        housekeeping = (
            STEP_TYPE_SYSTEM_MESSAGE,
            STEP_TYPE_CHECKPOINT,
            STEP_TYPE_FINISH,
        )
        eff_sql = next(
            (
                (t, s)
                for (t, s) in tail_rows
                if not (s == STATUS_DONE and t in housekeeping)
            ),
            tail_rows[0],
        )
      if (
          not is_child
          and not kept
          and "step_payload" in cols
          and not (
              eff_sql and eff_sql[1] in (STATUS_CANCELED, STATUS_INTERRUPTED)
          )
      ):
        plan_rows = con.execute(
            f"SELECT step_type, step_payload, {md_col} FROM steps "
            "WHERE idx > ? AND status = ? AND step_type IN (?, ?) "
            "ORDER BY idx DESC",
            (
                last_ui_idx,
                STATUS_DONE,
                STEP_TYPE_GENERIC,
                STEP_TYPE_CODE_ACTION,
            ),
        ).fetchall()
        seen_artifacts: set[str] = set()
        for st_type, pl_blob, md_blob in plan_rows:
          art_key, req_fb = _artifact_edit_feedback(st_type, pl_blob)
          if art_key is None or art_key in seen_artifacts:
            continue
          seen_artifacts.add(art_key)
          if req_fb:
            pending_plan = True
            plan_step_ts = _step_metadata_iso_ts(md_blob)
            break
    except sqlite3.Error:
      has_db = False
    finally:
      con.close()

  entries = read_last_transcript_entries(data_dir, conv_id, config=config)
  last, last_ts = _trailing_transcript_entry_and_ts(entries)
  if eff_sql and eff_sql[1] in (STATUS_CANCELED, STATUS_INTERRUPTED):
    return (
        CLI_STATE_SETTLED,
        "waiting for user input",
        last_ts,
        has_db,
        True,
        tuple(wf_ids),
    )

  if not entries or last is None:
    if eff_sql and eff_sql[0] == STEP_TYPE_USER_INPUT:
      return (
          CLI_STATE_WORKING,
          "turn starting",
          "",
          has_db,
          False,
          tuple(wf_ids),
      )
    if pending_plan:
      return (
          CLI_STATE_PLAN,
          "waiting for plan approval",
          plan_step_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return (
        CLI_STATE_SETTLED,
        "waiting for user input",
        "",
        has_db,
        False,
        tuple(wf_ids),
    )

  st = last.get("status") or ""
  tp = last.get("type") or ""
  if st == "WAITING":
    return (
        CLI_STATE_PERMISSION,
        "waiting on permission check",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )
  if st in ("PENDING", "RUNNING"):
    return (
        CLI_STATE_WORKING,
        f"{tp.lower()} is {st.lower()}",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )
  if st == "CANCELED":
    return (
        CLI_STATE_SETTLED,
        "waiting for user input",
        last_ts,
        has_db,
        True,
        tuple(wf_ids),
    )
  if tp == "PLANNER_RESPONSE":
    if last.get("tool_calls"):
      msg = (
          "running tool calls"
          if has_db
          else "waiting on tool call or confirmation"
      )
      return CLI_STATE_WORKING, msg, last_ts, has_db, False, tuple(wf_ids)
    if pending_plan:
      return (
          CLI_STATE_PLAN,
          "waiting for plan approval",
          last_ts or plan_step_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return (
        CLI_STATE_SETTLED,
        "waiting for user input",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )
  if tp == "ERROR_MESSAGE":
    if _is_recent_iso_timestamp(last.get("created_at") or ""):
      return (
          CLI_STATE_WORKING,
          "model error/retry in progress",
          last_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return (
        CLI_STATE_SETTLED,
        "waiting for user input (after error)",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )

  if tp == "GENERIC" and _is_parked_on_swarm_wait(entries, has_db=has_db):
    if is_child:
      return (
          CLI_STATE_SETTLED,
          "parked on swarm_wait",
          last_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return (
        CLI_STATE_WORKING,
        "waiting on swarm board",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )

  return (
      CLI_STATE_WORKING,
      f"turn in progress after {tp.lower()}",
      last_ts,
      has_db,
      False,
      tuple(wf_ids),
  )


def check_db_and_transcript_state(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[str, str, str, bool]:
  """Returns (state_str, reason_str, last_created_at, has_db)."""
  state, reason, last_ts, has_db, _, _ = _inspect_trajectory_state(
      data_dir, conv_id, config=config
  )
  return state, reason, last_ts, has_db


def check_db_and_transcript_settled(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[bool, str, str, list[dict]]:
  """Returns (settled_bool, reason, last_created_at, entries)."""
  state, reason, last_ts, _, _, _ = _inspect_trajectory_state(
      data_dir, conv_id, config=config
  )
  entries = read_transcript_entries(data_dir, conv_id, config=config)
  return state in CLI_SETTLED_STATES, reason, last_ts, entries


def _subagent_state_from_entries(
    entries: list[dict],
) -> tuple[list[str], set[str]]:
  """Returns (active_ids_list, killed_ids_set) from transcript entries."""
  sub_re = re.compile(r'"conversationId"\s*:\s*"([0-9a-fA-F-]{36})"')
  active: list[str] = []
  killed: set[str] = set()
  expect_subagent_output = 0
  for entry in entries:
    for tc in entry.get("tool_calls") or []:
      name = tc.get("name") or ""
      args = tc.get("args") or {}
      if name == "invoke_subagent":
        expect_subagent_output += 1
      elif name == "manage_subagents":
        action = (args.get("Action") or "").strip('"')
        if action == "kill_all":
          killed.update(active)
          active.clear()
        elif action == "kill":
          raw_ids = args.get("ConversationIds") or ""
          if isinstance(raw_ids, list):
            kill_ids = {str(x).strip('"') for x in raw_ids}
          else:
            kill_ids = set(UUID_RE.findall(str(raw_ids))) | set(
                re.findall(r"[0-9a-fA-F-]{36}", str(raw_ids))
            )
          killed.update(kill_ids)
          active = [s for s in active if s not in kill_ids]
    if expect_subagent_output > 0 and entry.get("type") == "GENERIC":
      content = entry.get("content") or ""
      m = sub_re.search(content)
      if m:
        cid = m.group(1)
        killed.discard(cid)
        if cid not in active:
          active.append(cid)
        expect_subagent_output -= 1
  return active, killed


def live_subagents_from_transcript(entries: list[dict]) -> list[str]:
  """Returns list of subagent conversationIds invoked and not later killed."""
  active, _ = _subagent_state_from_entries(entries)
  return active


def _subagent_label(cid: str, data: Mapping) -> str:
  desc = data.get("subagentDescriptor")
  if isinstance(desc, dict):
    for key in ("role", "typeName"):
      val = " ".join(str(desc.get(key) or "").split())
      if val and len(val) <= 20:
        return f"{val} {cid[:8]}"
  return cid[:8]


def _subagent_registry_entries(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[bool, list[tuple[str, str, float]], set[str]]:
  """Returns `(has_registry_dir, [(cid, label, mtime)], killed_set)`."""
  has_dir = False
  alive: list[tuple[str, str, float]] = []
  killed: set[str] = set()
  for bdir in _brain_dirs_for_conv(data_dir, conv_id, config=config):
    sub_dir = os.path.join(bdir, ".system_generated", "subagents")
    if not os.path.isdir(sub_dir):
      continue
    has_dir = True
    try:
      names = sorted(os.listdir(sub_dir))
    except OSError:
      continue
    for name in names:
      if not name.endswith(".json"):
        continue
      path = os.path.join(sub_dir, name)
      try:
        st = os.stat(path)
        with open(path, encoding="utf-8", errors="replace") as f:
          data = json.load(f)
      except (OSError, ValueError):
        continue
      if not isinstance(data, dict):
        continue
      cid = data.get("conversationId") or ""
      if not UUID_RE.match(cid):
        continue
      if data.get("state") == "SUBAGENT_STATE_KILLED":
        killed.add(cid)
      else:
        alive.append((cid, _subagent_label(cid, data), st.st_mtime))
  return has_dir, alive, killed


def _child_active_in_cli_lifetime(
    data_dir: str | None,
    child_id: str,
    reg_mtime: float | None,
    open_dbs: set[str],
    min_epoch: float | None,
    config: HarnessConfig | None = None,
) -> bool:
  """True when `child_id` was open or modified in the CLI process lifetime."""
  if min_epoch is None or child_id in open_dbs:
    return True
  mtimes: list[float] = []
  if reg_mtime is not None:
    mtimes.append(reg_mtime)
  for d in harness_data_dirs(data_dir, config=config):
    for path in (
        os.path.join(d, "conversations", f"{child_id}.db-wal"),
        os.path.join(d, "conversations", f"{child_id}.db"),
        os.path.join(
            d,
            "brain",
            child_id,
            ".system_generated",
            "logs",
            "transcript.jsonl",
        ),
    ):
      try:
        mtimes.append(os.path.getmtime(path))
      except OSError:
        pass
    undeliv = os.path.join(
        d, "brain", child_id, ".system_generated", "messages", "undelivered"
    )
    if os.path.isdir(undeliv):
      try:
        for name in os.listdir(undeliv):
          p = os.path.join(undeliv, name)
          try:
            st = os.stat(p)
          except OSError:
            continue
          if stat.S_ISREG(st.st_mode):
            mtimes.append(st.st_mtime)
      except OSError:
        pass
  return bool(mtimes) and max(mtimes) >= min_epoch


def _transcript_subagent_state(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[list[str], set[str]]:
  path = _transcript_path(data_dir, conv_id, config=config)
  if not os.path.isfile(path):
    return [], set()
  try:
    with open(path, "rb") as f:
      raw = f.read()
    if b"invoke_subagent" not in raw and b"manage_subagents" not in raw:
      return [], set()
    entries = []
    for raw_line in raw.splitlines():
      if (
          b"invoke_subagent" not in raw_line
          and b"manage_subagents" not in raw_line
          and b"conversationId" not in raw_line
      ):
        continue
      try:
        entries.append(json.loads(raw_line.decode("utf-8", "replace")))
      except ValueError:
        continue
    return _subagent_state_from_entries(entries)
  except OSError:
    return [], set()


def discover_active_child_agents(
    data_dir: str | None,
    root_conv_id: str,
    open_dbs: set[str],
    cli_start_epoch: float | None = None,
    extra_seed_ids: tuple[str, ...] = (),
    config: HarnessConfig | None = None,
) -> list[tuple[str, str]]:
  """BFS over live subagents, workflow children, and nested helpers."""
  min_epoch = (cli_start_epoch - 2.0) if cli_start_epoch is not None else None
  visited = {root_conv_id}
  killed_all: set[str] = set()
  result: list[tuple[str, str]] = []
  queue = [root_conv_id]
  sorted_open = sorted(open_dbs)

  while queue:
    cur_id = queue.pop(0)
    has_reg, alive, killed = _subagent_registry_entries(
        data_dir, cur_id, config=config
    )
    killed_all.update(killed)

    cands: list[tuple[str, str, float | None]] = [
        (cid, label, mtime) for cid, label, mtime in alive
    ]
    if cur_id == root_conv_id:
      for cid in extra_seed_ids:
        if cid and UUID_RE.match(cid):
          cands.append((cid, f"workflow {cid[:8]}", None))

    if not has_reg:
      tx_active, tx_killed = _transcript_subagent_state(
          data_dir, cur_id, config=config
      )
      killed_all.update(tx_killed)
      for cid in tx_active:
        cands.append((cid, cid[:8], None))

    for db_id in sorted_open:
      if db_id in visited or db_id in killed_all:
        continue
      parent, root = get_trajectory_parents(data_dir, db_id, config=config)
      if parent == cur_id or (cur_id == root_conv_id and root == root_conv_id):
        cands.append((db_id, db_id[:8], None))

    for cid, label, reg_mtime in cands:
      if cid in visited or cid in killed_all:
        continue
      if not _child_active_in_cli_lifetime(
          data_dir, cid, reg_mtime, open_dbs, min_epoch, config=config
      ):
        continue
      visited.add(cid)
      result.append((cid, label))
      queue.append(cid)

  return result


def live_subagents_for_conv(
    data_dir: str | None,
    conv_id: str,
    open_dbs: set[str],
    cli_start_epoch: float | None = None,
    extra_seed_ids: tuple[str, ...] = (),
    config: HarnessConfig | None = None,
) -> list[str]:
  """Returns live subagent conversation IDs belonging to `conv_id`."""
  return [
      cid
      for cid, _ in discover_active_child_agents(
          data_dir,
          conv_id,
          open_dbs,
          cli_start_epoch=cli_start_epoch,
          extra_seed_ids=extra_seed_ids,
          config=config,
      )
  ]


def classify_cli_state_with_ts(
    cli_info: Mapping,
    is_print: bool,
    pane_id: str,
    conv_id: str | None,
    open_dbs: set[str],
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    mcp_sigs: set[tuple[str, str | None]],
    self_ignored: set[int],
    data_dir: str | None,
    config: HarnessConfig | None = None,
) -> tuple[str, str, str]:
  """Returns (state_str, reason_str, last_ts_str) for one coding-agent CLI."""
  if is_print:
    return CLI_STATE_WORKING, "headless --print run in progress", ""

  cli_start_epoch = cli_info.get("start_epoch")
  min_epoch = (cli_start_epoch - 2.0) if cli_start_epoch is not None else None

  if conv_id is None:
    kids = active_cli_child_processes(
        cli_info["pid"], procs, children_map, mcp_sigs, self_ignored
    )
    if kids:
      sample = kids[0]["comm"]
      return (
          CLI_STATE_WORKING,
          f"active child task ({sample}, pid {kids[0]['pid']})",
          "",
      )
    return CLI_STATE_SETTLED, "empty prompt (no conversation yet)", ""

  state, reason, parent_ts, has_db, parent_canceled, wf_ids = (
      _inspect_trajectory_state(data_dir, conv_id, config=config)
  )
  if state == CLI_STATE_PERMISSION:
    return state, reason, parent_ts

  best_sub_perm: tuple[str, str, str] | None = None
  best_sub_question: tuple[str, str, str] | None = None
  saw_sub_working: tuple[str, str] | None = None
  for sub_id, sub_label in discover_active_child_agents(
      data_dir,
      conv_id,
      open_dbs,
      cli_start_epoch=cli_start_epoch,
      extra_seed_ids=wf_ids,
      config=config,
  ):
    sub_state, sub_reason, sub_ts, _, _, _ = _inspect_trajectory_state(
        data_dir, sub_id, is_child=True, config=config
    )
    if sub_state == CLI_STATE_PERMISSION:
      if (
          best_sub_perm is None
          or _iso_gt(sub_ts, best_sub_perm[2])
          or (bool(sub_ts) and not best_sub_perm[2])
      ):
        best_sub_perm = (
            CLI_STATE_PERMISSION,
            f"subagent {sub_label} {sub_reason}",
            sub_ts,
        )
    elif sub_state == CLI_STATE_QUESTION:
      if (
          best_sub_question is None
          or _iso_gt(sub_ts, best_sub_question[2])
          or (bool(sub_ts) and not best_sub_question[2])
      ):
        best_sub_question = (
            CLI_STATE_QUESTION,
            f"subagent {sub_label} {sub_reason}",
            sub_ts,
        )
    elif sub_state == CLI_STATE_WORKING and saw_sub_working is None:
      saw_sub_working = (
          CLI_STATE_WORKING,
          f"subagent {sub_label} active ({sub_reason})",
      )
    elif sub_state == CLI_STATE_SETTLED and saw_sub_working is None:
      if has_wakeable_undelivered_message(
          data_dir, sub_id, min_epoch=min_epoch, config=config
      ):
        saw_sub_working = (
            CLI_STATE_WORKING,
            f"subagent {sub_label} wakeup pending",
        )
      elif (
          state in CLI_SETTLED_STATES
          and not parent_canceled
          and _iso_gt(sub_ts, parent_ts)
          and _is_recent_iso_timestamp(sub_ts, max_age_sec=5.0)
      ):
        saw_sub_working = (
            CLI_STATE_WORKING,
            f"subagent {sub_label} wakeup pending",
        )

  if best_sub_perm is not None:
    return (
        CLI_STATE_PERMISSION,
        best_sub_perm[1],
        _newer_iso_ts(parent_ts, best_sub_perm[2]),
    )
  if state == CLI_STATE_QUESTION:
    if best_sub_question is not None and _iso_gt(
        best_sub_question[2], parent_ts
    ):
      return (
          CLI_STATE_QUESTION,
          best_sub_question[1],
          _newer_iso_ts(parent_ts, best_sub_question[2]),
      )
    return state, reason, parent_ts
  if best_sub_question is not None:
    return (
        CLI_STATE_QUESTION,
        best_sub_question[1],
        _newer_iso_ts(parent_ts, best_sub_question[2]),
    )

  kids = active_cli_child_processes(
      cli_info["pid"], procs, children_map, mcp_sigs, self_ignored
  )
  if kids:
    sample = kids[0]["comm"]
    return (
        CLI_STATE_WORKING,
        f"active child task ({sample}, pid {kids[0]['pid']})",
        parent_ts,
    )

  # Optional statusLine state file fallback / extra task check if present.
  pane_state_path = _pane_state_path(pane_id)
  if os.path.isfile(pane_state_path):
    try:
      with open(pane_state_path, encoding="utf-8") as f:
        st = json.load(f)
      if st.get("conversation_id") == conv_id:
        ag = st.get("agent_state") or ""
        if not has_db:
          if st.get("tool_confirmation_pending"):
            return (
                CLI_STATE_PERMISSION,
                "waiting on permission check",
                parent_ts,
            )
          if ag == "tool_use":
            return (
                CLI_STATE_QUESTION,
                "waiting on tool confirmation or question",
                parent_ts,
            )
          if ag == "working":
            return CLI_STATE_WORKING, "agent actively working", parent_ts
        if (st.get("task_count") or 0) > 0:
          return (
              CLI_STATE_WORKING,
              f"{st['task_count']} background task(s) active",
              parent_ts,
          )
        for sub in st.get("subagents") or []:
          if sub.get("status") == "running":
            return (
                CLI_STATE_WORKING,
                f"subagent running ({sub.get('role') or 'subagent'})",
                parent_ts,
            )
        if (st.get("pending_input_count") or 0) > 0:
          return CLI_STATE_WORKING, "queued input pending", parent_ts
    except (OSError, ValueError):
      pass

  if state not in CLI_SETTLED_STATES:
    return state, reason, parent_ts
  if saw_sub_working is not None:
    return saw_sub_working[0], saw_sub_working[1], parent_ts
  if not parent_canceled and has_wakeable_undelivered_message(
      data_dir, conv_id, min_epoch=min_epoch, config=config
  ):
    return CLI_STATE_WORKING, "wakeup message pending", parent_ts
  return state, reason, parent_ts


def classify_cli_state(
    cli_info: Mapping,
    is_print: bool,
    pane_id: str,
    conv_id: str | None,
    open_dbs: set[str],
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    mcp_sigs: set[tuple[str, str | None]],
    self_ignored: set[int],
    data_dir: str | None,
    config: HarnessConfig | None = None,
) -> tuple[str, str]:
  """Returns (state_str, reason_str) where state_str is one of CLI_STATE_*."""
  state, reason, _ = classify_cli_state_with_ts(
      cli_info,
      is_print,
      pane_id,
      conv_id,
      open_dbs,
      procs,
      children_map,
      mcp_sigs,
      self_ignored,
      data_dir,
      config=config,
  )
  return state, reason


def classify_cli_instance(
    cli_info: Mapping,
    is_print: bool,
    pane_id: str,
    conv_id: str | None,
    open_dbs: set[str],
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    mcp_sigs: set[tuple[str, str | None]],
    self_ignored: set[int],
    data_dir: str | None,
    config: HarnessConfig | None = None,
) -> tuple[bool, str]:
  """Returns (settled_bool, reason_str)."""
  state, reason = classify_cli_state(
      cli_info,
      is_print,
      pane_id,
      conv_id,
      open_dbs,
      procs,
      children_map,
      mcp_sigs,
      self_ignored,
      data_dir,
      config=config,
  )
  return state in CLI_SETTLED_STATES, reason


def _sql_waiting_step_info(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[str | None, int | None, str, tuple[str, ...]]:
  """Returns `(wait_state_or_None, step_idx, sql_ts, wf_ids)` from SQLite."""
  con = open_conv_db(data_dir, conv_id, config=config)
  if con is None:
    return None, None, "", ()
  wf_ids: list[str] = []
  wf_seen: set[str] = set()
  try:
    latest = con.execute(
        "SELECT idx FROM steps ORDER BY idx DESC LIMIT 1"
    ).fetchone()
    if latest is None:
      return None, None, "", ()
    max_idx = latest[0]
    ui_row = con.execute(
        "SELECT MAX(idx) FROM steps WHERE step_type = ?",
        (STEP_TYPE_USER_INPUT,),
    ).fetchone()
    ui_idx = ui_row[0] if (ui_row and ui_row[0] is not None) else 0
    min_idx = min(ui_idx, max(0, max_idx - 25))
    ckpt_row = con.execute(
        "SELECT COALESCE(MAX(idx), 0) FROM steps "
        "WHERE step_type = ? AND status = ?",
        (STEP_TYPE_CHECKPOINT, STATUS_DONE),
    ).fetchone()
    ckpt_idx = ckpt_row[0] if (ckpt_row and ckpt_row[0] is not None) else 0
    lower_idx = min(min_idx, ckpt_idx)
    cols = {r[1] for r in con.execute("PRAGMA table_info(steps)").fetchall()}
    td_col = (
        "task_details" if "task_details" in cols else "NULL AS task_details"
    )
    pl_col = (
        "step_payload" if "step_payload" in cols else "NULL AS step_payload"
    )
    md_col = "metadata" if "metadata" in cols else "NULL AS metadata"
    rows = con.execute(
        f"SELECT idx, step_type, status, {td_col}, {pl_col}, {md_col} "
        "FROM steps WHERE idx >= ? AND status IN (?, ?, ?, ?, ?) "
        "ORDER BY idx ASC",
        (
            lower_idx,
            STATUS_PENDING,
            STATUS_RUNNING,
            STATUS_GENERATING,
            STATUS_WAITING,
            STATUS_QUEUED,
        ),
    ).fetchall()
    first_q: tuple[str, int, str] | None = None
    for row_idx, st_type, st_val, task_blob, payload, md_blob in rows:
      if st_val == STATUS_WAITING:
        keep = row_idx >= ckpt_idx
      elif task_blob:
        keep = row_idx >= ckpt_idx and not _is_daemon_task(task_blob)
      else:
        keep = row_idx >= min_idx
      if not keep:
        continue
      if st_type == STEP_TYPE_GENERIC and payload:
        for cid in _workflow_agent_ids_from_step(payload):
          if cid not in wf_seen:
            wf_seen.add(cid)
            wf_ids.append(cid)
      if st_val != STATUS_WAITING:
        continue
      w_state, _ = classify_waiting_step(st_type, payload)
      if w_state == CLI_STATE_PERMISSION:
        return (
            CLI_STATE_PERMISSION,
            row_idx,
            _step_metadata_iso_ts(md_blob),
            tuple(wf_ids),
        )
      if first_q is None:
        first_q = (w_state, row_idx, _step_metadata_iso_ts(md_blob))
    if first_q is not None:
      return first_q[0], first_q[1], first_q[2], tuple(wf_ids)
    return None, None, "", tuple(wf_ids)
  except sqlite3.Error:
    return None, None, "", ()
  finally:
    con.close()


def urgent_waiting_signature(
    data_dir: str | None,
    conv_id: str | None,
    open_dbs: set[str] | tuple[str, ...] = (),
    cli_start_epoch: float | None = None,
    config: HarnessConfig | None = None,
) -> str:
  """Returns stable `<conv_id>:<idx>:<sql_ts>` for winning urgent wait."""
  if not conv_id:
    return ""
  r_state, r_idx, r_sql_ts, wf_ids = _sql_waiting_step_info(
      data_dir, conv_id, config=config
  )
  if r_state == CLI_STATE_PERMISSION:
    return f"{conv_id}:{r_idx}:{r_sql_ts}" if r_sql_ts else ""

  parent_ts = r_sql_ts
  if r_state == CLI_STATE_QUESTION:
    _, parent_tx_ts = _trailing_transcript_entry_and_ts(
        read_last_transcript_entries(data_dir, conv_id, config=config)
    )
    parent_ts = _newer_iso_ts(r_sql_ts, parent_tx_ts)

  best_sub_perm: tuple[str, int | None, str, str] | None = None
  best_sub_question: tuple[str, int | None, str, str] | None = None
  for sub_id, _ in discover_active_child_agents(
      data_dir,
      conv_id,
      set(open_dbs),
      cli_start_epoch=cli_start_epoch,
      extra_seed_ids=wf_ids,
      config=config,
  ):
    s_state, s_idx, s_sql_ts, _ = _sql_waiting_step_info(
        data_dir, sub_id, config=config
    )
    if s_state not in (CLI_STATE_PERMISSION, CLI_STATE_QUESTION):
      continue
    _, s_tx_ts = _trailing_transcript_entry_and_ts(
        read_last_transcript_entries(data_dir, sub_id, config=config)
    )
    s_rank_ts = _newer_iso_ts(s_sql_ts, s_tx_ts)
    if s_state == CLI_STATE_PERMISSION:
      if (
          best_sub_perm is None
          or _iso_gt(s_rank_ts, best_sub_perm[3])
          or (bool(s_rank_ts) and not best_sub_perm[3])
      ):
        best_sub_perm = (sub_id, s_idx, s_sql_ts, s_rank_ts)
    elif (
        best_sub_question is None
        or _iso_gt(s_rank_ts, best_sub_question[3])
        or (bool(s_rank_ts) and not best_sub_question[3])
    ):
      best_sub_question = (sub_id, s_idx, s_sql_ts, s_rank_ts)

  winner: tuple[str, int | None, str] | None = None
  if best_sub_perm is not None:
    winner = best_sub_perm[:3]
  elif r_state == CLI_STATE_QUESTION:
    if best_sub_question is not None and _iso_gt(
        best_sub_question[3], parent_ts
    ):
      winner = best_sub_question[:3]
    else:
      winner = (conv_id, r_idx, r_sql_ts)
  elif best_sub_question is not None:
    winner = best_sub_question[:3]

  if winner and winner[2]:
    return f"{winner[0]}:{winner[1]}:{winner[2]}"
  return ""


def _decode_pbtxt_literal(raw_inner: str) -> str:
  """Unescapes a prototext string literal (preserving UTF-8 octal byte runs)."""
  out = bytearray()
  i = 0
  n = len(raw_inner)
  simple = {
      "a": 7,
      "b": 8,
      "f": 12,
      "n": 10,
      "r": 13,
      "t": 9,
      "v": 11,
      "\\": 92,
      "'": 39,
      '"': 34,
      "?": 63,
  }
  hex_digits = "0123456789abcdefABCDEF"
  while i < n:
    ch = raw_inner[i]
    if ch != "\\" or i + 1 >= n:
      out.extend(ch.encode("utf-8"))
      i += 1
      continue
    esc = raw_inner[i + 1]
    if esc in simple:
      out.append(simple[esc])
      i += 2
    elif "0" <= esc <= "7":
      end = i + 2
      while end < n and end < i + 4 and "0" <= raw_inner[end] <= "7":
        end += 1
      out.append(int(raw_inner[i + 1 : end], 8) & 0xFF)
      i = end
    elif esc in ("x", "X"):
      end = i + 2
      while end < n and end < i + 4 and raw_inner[end] in hex_digits:
        end += 1
      if end > i + 2:
        out.append(int(raw_inner[i + 2 : end], 16) & 0xFF)
        i = end
      else:
        out.extend(b"\\" + esc.encode("utf-8"))
        i += 2
    elif esc in ("u", "U"):
      width = 4 if esc == "u" else 8
      hex_s = raw_inner[i + 2 : i + 2 + width]
      if len(hex_s) == width and all(c in hex_digits for c in hex_s):
        cp = int(hex_s, 16)
        if cp <= 0x10FFFF:
          out.extend(chr(cp).encode("utf-8"))
          i += 2 + width
          continue
      out.extend(b"\\" + esc.encode("utf-8"))
      i += 2
    else:
      out.extend(b"\\" + esc.encode("utf-8"))
      i += 2
  return out.decode("utf-8", "replace")


def _pbtxt_title(text: str) -> str:
  """Extracts top-level `title: "..."` / `'...'` from prototext content."""
  matches = list(re.finditer(r"^([ \t]*)title\s*:\s*([\"'])", text, re.M))
  if not matches:
    return ""
  chosen = next((m for m in matches if m.group(1) == ""), matches[0])
  n = len(text)
  q = chosen.group(2)
  start = j = chosen.start(2) + 1
  raw_chunks: list[str] = []
  while True:
    while j < n and text[j] != q:
      if text[j] == "\\":
        j += 2
      else:
        j += 1
    if j >= n:
      return ""
    raw_chunks.append(text[start:j])
    k = j + 1
    while k < n and text[k] in " \t\r\n":
      k += 1
    if k < n and text[k] in ('"', "'"):
      q = text[k]
      start = j = k + 1
      continue
    break
  return _decode_pbtxt_literal("".join(raw_chunks))


def _annotation_title(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> str:
  """Returns custom title from `annotations/<conv_id>.{pbtxt,json}`."""
  for d in harness_data_dirs(data_dir, config=config):
    pb_path = os.path.join(d, "annotations", f"{conv_id}.pbtxt")
    if os.path.isfile(pb_path):
      try:
        with open(pb_path, encoding="utf-8", errors="replace") as f:
          title = _pbtxt_title(f.read()).strip()
        if title:
          return title
      except OSError:
        pass
    for ext in (".json", ""):
      path = os.path.join(d, "annotations", f"{conv_id}{ext}")
      if not os.path.isfile(path):
        continue
      try:
        with open(path, encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict):
          title = (data.get("title") or "").strip()
          if title:
            return title
      except (OSError, ValueError):
        continue
  return ""


def _first_user_input_label(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> str:
  """Streams transcript.jsonl from the top and stops at the first USER_INPUT."""
  path = _transcript_path(data_dir, conv_id, config=config)
  if not os.path.isfile(path):
    return ""
  try:
    with open(path, encoding="utf-8", errors="replace") as f:
      for line in f:
        if "USER_INPUT" not in line:
          continue
        try:
          entry = json.loads(line)
        except ValueError:
          continue
        if entry.get("type") != "USER_INPUT":
          continue
        text = entry.get("content") or ""
        m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.S)
        if m:
          text = m.group(1)
        if text.strip():
          return text
        break
  except OSError:
    return ""
  return ""


def conversation_labels(
    data_dir: str | None,
    conv_ids: list[str],
    max_len: int = 80,
    config: HarnessConfig | None = None,
    include_user_input: bool = True,
) -> dict[str, str]:
  """Batch lookup of `{conv_id: title}` opening summaries DB at most once."""
  unique_ids: list[str] = []
  seen: set[str] = set()
  for cid in conv_ids:
    if cid and UUID_RE.match(cid) and cid not in seen:
      seen.add(cid)
      unique_ids.append(cid)
  if not unique_ids:
    return {}

  found: dict[str, str] = {}
  for cid in unique_ids:
    ann = _annotation_title(data_dir, cid, config=config)
    if ann:
      found[cid] = (
          _squash_line(ann, max_len) if max_len else " ".join(ann.split())
      )

  remaining = [cid for cid in unique_ids if cid not in found]
  for d in harness_data_dirs(data_dir, config=config):
    if not remaining:
      break
    sum_db = os.path.join(d, "conversation_summaries.db")
    if not os.path.isfile(sum_db):
      continue
    try:
      uri = pathlib.Path(sum_db).as_uri() + "?mode=ro"
      con = sqlite3.connect(uri, uri=True, timeout=0.5)
      try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        for tbl in tables:
          if not remaining:
            break
          cols = {
              r[1] for r in con.execute(f"PRAGMA table_info({tbl})").fetchall()
          }
          id_col = next(
              (
                  c
                  for c in ("cascade_id", "conversation_id", "id")
                  if c in cols
              ),
              None,
          )
          title_col = next(
              (c for c in ("title", "summary") if c in cols), None
          )
          if not (id_col and title_col):
            continue
          placeholders = ",".join("?" for _ in remaining)
          rows = con.execute(
              f"SELECT {id_col}, {title_col} FROM {tbl} "
              f"WHERE {id_col} IN ({placeholders})",
              remaining,
          ).fetchall()
          for cid, raw_title in rows:
            if (
                cid
                and raw_title
                and str(raw_title).strip()
                and cid not in found
            ):
              raw_s = str(raw_title)
              found[cid] = (
                  _squash_line(raw_s, max_len)
                  if max_len
                  else " ".join(raw_s.split())
              )
          remaining = [cid for cid in remaining if cid not in found]
      finally:
        con.close()
    except sqlite3.Error:
      pass

  if include_user_input:
    for cid in remaining:
      text = _first_user_input_label(data_dir, cid, config=config)
      if text:
        found[cid] = (
            _squash_line(text, max_len) if max_len else " ".join(text.split())
        )

  return found


def conversation_label(
    data_dir: str | None,
    conv_id: str | None,
    max_len: int = 64,
    config: HarnessConfig | None = None,
) -> str:
  """Short human-readable summary/title for `conv_id`, if available."""
  if not conv_id:
    return "(new prompt)"
  return conversation_labels(
      data_dir, [conv_id], max_len=max_len, config=config
  ).get(conv_id, "")


def _squash_line(text: str, limit: int) -> str:
  single = " ".join(text.split())
  if len(single) <= limit:
    return single
  return single[: limit - 3] + "..."


_GH_REMOTE_RE = re.compile(
    r"^(?:https?://github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GERRIT_REMOTE_RE = re.compile(
    r"^https?://([a-z0-9-]+?)(?:-review)?\.googlesource\.com(?:/|$)",
    re.IGNORECASE,
)
_INLINE_GERRIT_URL_RE = re.compile(
    r"(?:"
    r"<\s*(?:https?://)?([a-z0-9-]+)-review\.googlesource\.com"
    r"(?:/c/[^\s<>]+?/\+/([1-9]\d*)|/(?:c/)?([1-9]\d*))"
    r"(?:[/#?][^\s<>)\]}\"']*)?\s*>"
    r"|"
    r"(?:https?://)?([a-z0-9-]+)-review\.googlesource\.com"
    r"(?:/c/[^\s<>]+?/\+/([1-9]\d*)|/(?:c/)?([1-9]\d*))"
    r"(?:[/#?][^\s<>)\]}\"']*(?<![.,;:!?]))?"
    r")",
    re.IGNORECASE,
)
_INLINE_GERRIT_HASH_RE = re.compile(
    r"\b([a-z0-9-]+)-review#([1-9]\d*)\b", re.IGNORECASE
)
_INLINE_DART_HASH_RE = re.compile(
    r"(?<![/\w.-])(?:dart-sdk|sdk)#([1-9]\d*)\b", re.IGNORECASE
)
_INLINE_GH_URL_RE = re.compile(
    r"(?:"
    r"<\s*(?:https?://)?github\.com/"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)"
    r"(?:[/#?][^\s<>)\]}\"']*)?\s*>"
    r"|"
    r"(?:https?://)?github\.com/"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)"
    r"(?:[/#?][^\s<>)\]}\"']*(?<![.,;:!?]))?"
    r")",
    re.IGNORECASE,
)
_INLINE_PR_PULL_RE = re.compile(
    r"(?<![/\w.-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)"
    r"(?:[/#?][^\s<>)\]}\"']*(?<![.,;:!?]))?",
    re.IGNORECASE,
)
_INLINE_REPO_HASH_RE = re.compile(
    r"(?<![/\w.-])([A-Za-z0-9_.-]+)#([1-9]\d*)\b"
)

_LEADING_TAG_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
_LEADING_EMOJI_RE = re.compile(
    r"^(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF"
    r"\u2B00-\u2BFF\u2190-\u21FF\u200D\uFE0F]+\s*)+"
)
_REVIEW_TITLE_PREFIX_RE = re.compile(
    r"^Review\s+("
    r"[a-z0-9-]+-review/\d+"
    r"|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+"
    r"|[A-Za-z0-9_.-]+#\d+"
    r"|[a-z]{1,4}/\d+"
    r")(?:\b|:|$)",
    re.IGNORECASE,
)
_ANCHOR_ID_RE = re.compile(
    r"(?:"
    r"\b[a-z0-9-]+-review/\d+"
    r"|(?<![/\w.-])[A-Za-z0-9_.-]+#\d+"
    r"|(?<![/\w.-])[a-z]{1,4}/\d+(?![/\w.-])"
    r")"
)
_GERRIT_TARGET_RE = re.compile(r"^([a-z0-9-]+)-review/(\d+)$", re.IGNORECASE)
_GH_SLUG_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$")
_REPO_HASH_RE = re.compile(r"^([A-Za-z0-9_.-]+)#([1-9]\d*)$")
_SHORT_REF_RE = re.compile(r"^([a-z]{1,4})/(\d+)$", re.IGNORECASE)
_TITLE_STOPWORDS = frozenset({
    "for", "in", "on", "to", "of", "with", "and",
    "the", "a", "an", "from", "by", "at",
})
_TITLE_FILLER_WORDS = frozenset({
    "please", "help", "can", "you", "investigate", "investigating",
})
_GENERIC_REVIEW_TITLES = frozenset({
    "code review",
    "code review request",
    "code review session",
    "code review task",
    "start code review",
    "start-code-review",
    "review",
    "review request",
    "reviewing pr",
    "reviewing a pr",
    "reviewing change",
    "reviewing a change",
    "reviewing code",
    "pr review",
    "code reviewing",
    "new conversation",
    "untitled",
})
_REVIEW_CMD_PREFIX_RE = re.compile(
    r"^/(?:start-code-review|review-github-pr|review-pr)\b",
    re.IGNORECASE,
)


def default_repos_dir() -> pathlib.Path:
  """Resolves local git checkouts root from `$REVIEW_REPOS_DIR` or `~/repos`."""
  env_repos = os.environ.get("REVIEW_REPOS_DIR", "").strip()
  if env_repos:
    return pathlib.Path(os.path.expanduser(env_repos))
  return pathlib.Path.home() / "repos"


def _run_git_remote_url(repo_dir: pathlib.Path, remote: str) -> str | None:
  try:
    proc = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_dir,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return None
  if proc.returncode != 0:
    return None
  return proc.stdout.strip() or None


def _resolve_repo_hash_target(
    repo: str, num: int, repos_path: pathlib.Path
) -> str | None:
  for cand in (repos_path / repo, repos_path / repo / "sdk"):
    if not (cand.is_dir() and (cand / ".git").exists()):
      continue
    upstream = _run_git_remote_url(cand, "upstream")
    if upstream:
      m = _GH_REMOTE_RE.fullmatch(upstream)
      if m:
        return f"{m.group(1).lower()}/{m.group(2).lower()}#{num}"
    origin = _run_git_remote_url(cand, "origin")
    if origin:
      m = _GH_REMOTE_RE.fullmatch(origin)
      if m:
        return f"{m.group(1).lower()}/{m.group(2).lower()}#{num}"
      mg = _GERRIT_REMOTE_RE.match(origin)
      if mg:
        return f"{mg.group(1).lower().removesuffix('-review')}-review/{num}"
    if upstream:
      mg = _GERRIT_REMOTE_RE.match(upstream)
      if mg:
        return f"{mg.group(1).lower().removesuffix('-review')}-review/{num}"
    return None
  return None


def normalize_inline_review_refs(
    text: str, repos_dir: str | pathlib.Path | None = None
) -> str:
  """Replaces Gerrit/GitHub PR URLs and shorthands with short IDs."""
  if not text:
    return ""
  repos_path = (
      pathlib.Path(os.path.expanduser(str(repos_dir)))
      if repos_dir is not None
      else default_repos_dir()
  )

  def _gerrit_sub(m: re.Match[str]) -> str:
    host = (m.group(1) or m.group(4)).lower()
    num = m.group(2) or m.group(3) or m.group(5) or m.group(6)
    return f"{host}-review/{int(num)}"

  def _gh_sub(m: re.Match[str]) -> str:
    owner = (m.group(1) or m.group(4)).lower()
    repo = (m.group(2) or m.group(5)).lower()
    num = int(m.group(3) or m.group(6))
    return f"{owner}/{repo}#{num}"

  out = _INLINE_GERRIT_URL_RE.sub(_gerrit_sub, text)
  out = _INLINE_GERRIT_HASH_RE.sub(
      lambda m: f"{m.group(1).lower()}-review/{int(m.group(2))}", out
  )
  out = _INLINE_GH_URL_RE.sub(_gh_sub, out)
  out = _INLINE_PR_PULL_RE.sub(
      lambda m: f"{m.group(1).lower()}/{m.group(2).lower()}#{int(m.group(3))}",
      out,
  )
  out = _INLINE_DART_HASH_RE.sub(
      lambda m: f"dart-review/{int(m.group(1))}", out
  )

  cache: dict[tuple[str, int], str] = {}

  def _repo_sub(m: re.Match[str]) -> str:
    key = (m.group(1), int(m.group(2)))
    if key not in cache:
      cache[key] = (
          _resolve_repo_hash_target(key[0], key[1], repos_path) or m.group(0)
      )
    return cache[key]

  return _INLINE_REPO_HASH_RE.sub(_repo_sub, out)


def _strip_leading_title_prefixes(title: str) -> str:
  s = title.strip()
  while s:
    prev = s
    s = _LEADING_TAG_RE.sub("", s).strip()
    s = _LEADING_EMOJI_RE.sub("", s).strip()
    if s == prev:
      break
  return s


def is_generic_review_title(title: str | None) -> bool:
  """True when `title` is empty, generic review boilerplate, or a slash cmd."""
  if not title or not str(title).strip():
    return True
  s = _strip_leading_title_prefixes(" ".join(str(title).split()))
  if not s:
    return True
  if _REVIEW_TITLE_PREFIX_RE.match(s):
    return False
  if _REVIEW_CMD_PREFIX_RE.match(s) or re.match(
      r"^(?:code[- ]review|reviewing|start[- ]code[- ]review|pr[- ]review)\b",
      s,
      re.IGNORECASE,
  ):
    return True
  return s.strip(".:!?\"' ").lower() in _GENERIC_REVIEW_TITLES


def _compact_review_target(target_str: str, max_len: int = 22) -> str:
  s = target_str.strip()
  m = _GH_SLUG_RE.fullmatch(s)
  if m:
    repo = m.group(2)
    suffix = f"#{m.group(3)}"
    full = f"{repo}{suffix}"
    if len(full) <= max_len:
      return full
    if len(suffix) < max_len:
      return f"{repo[: max_len - len(suffix)]}{suffix}"
    return full[:max_len]

  m = _REPO_HASH_RE.fullmatch(s)
  if m:
    repo = m.group(1)
    suffix = f"#{m.group(2)}"
    full = f"{repo}{suffix}"
    if len(full) <= max_len:
      return full
    if len(suffix) < max_len:
      return f"{repo[: max_len - len(suffix)]}{suffix}"
    return full[:max_len]

  m = _GERRIT_TARGET_RE.fullmatch(s)
  if m:
    host = m.group(1).lower()
    num = m.group(2)
    full = f"{host}-review/{num}"
    if len(full) <= max_len:
      return full
    short = f"{host}/{num}"
    if len(short) <= max_len:
      return short
    slash_num = f"/{num}"
    if len(slash_num) < max_len:
      return f"{host[: max_len - len(slash_num)]}{slash_num}"
    return short[:max_len]

  m = _SHORT_REF_RE.fullmatch(s)
  if m:
    norm = f"{m.group(1).lower()}/{m.group(2)}"
    if len(norm) <= max_len:
      return norm
    return norm[: max_len - 1] + "…"

  if len(s) <= max_len:
    return s
  return s[: max_len - 1] + "…"


def _clean_word_token(word: str) -> str:
  return word.strip(".,;:!?()[]{}\"'")


def _compact_with_anchor(text: str, max_len: int) -> str:
  m = _ANCHOR_ID_RE.search(text)
  if not m:
    return ""
  anchor = m.group(0)
  gh = _GH_SLUG_RE.fullmatch(anchor)
  if gh:
    anchor = f"{gh.group(2)}#{gh.group(3)}"
  gm = _GERRIT_TARGET_RE.fullmatch(anchor)
  if gm and len(anchor) > max_len:
    anchor = f"{gm.group(1).lower()}/{gm.group(2)}"
  if len(anchor) > max_len:
    return anchor[: max_len - 1] + "…"
  if len(anchor) == max_len:
    return anchor

  def _sig_tokens(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
      cleaned = tok.strip(",;:")
      low = _clean_word_token(cleaned).lower()
      if low and low not in _TITLE_STOPWORDS:
        out.append(cleaned)
    return out

  left = _sig_tokens(text[: m.start()].split())
  right = _sig_tokens(text[m.end() :].split())
  left_indexed = [(-1 - idx, tok) for idx, tok in enumerate(left)]
  right_indexed = [(idx + 1, tok) for idx, tok in enumerate(right)]
  candidates: list[tuple[int, str]] = []
  if len(left_indexed) <= 2:
    candidates.extend(left_indexed)
    candidates.extend(right_indexed)
  else:
    left_no_filler = [
        item
        for item in left_indexed
        if _clean_word_token(item[1]).lower() not in _TITLE_FILLER_WORDS
    ]
    left_pool = list(reversed(left_no_filler or left_indexed))
    right_pool = list(right_indexed)
    while left_pool or right_pool:
      if right_pool:
        candidates.append(right_pool.pop(0))
      if left_pool:
        candidates.append(left_pool.pop(0))

  chosen: list[tuple[int, str]] = [(0, anchor)]
  cur_len = len(anchor)
  for pos, tok in candidates:
    if cur_len + 1 + len(tok) <= max_len:
      chosen.append((pos, tok))
      cur_len += 1 + len(tok)

  chosen.sort(key=lambda item: item[0])
  return " ".join(tok for _, tok in chosen)


def compact_window_title(
    title: str | None,
    max_len: int = 22,
    repos_dir: str | pathlib.Path | None = None,
) -> str:
  """Compacts a conversation title for a narrow tmux window tab."""
  if not title or not str(title).strip():
    return ""
  s = _strip_leading_title_prefixes(" ".join(str(title).split()))
  if not s:
    return ""
  s = normalize_inline_review_refs(s, repos_dir=repos_dir)
  s = re.sub(
      r"(?<![/\w.-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b",
      lambda m: f"{m.group(2)}#{m.group(3)}",
      s,
  )

  m_rev = _REVIEW_TITLE_PREFIX_RE.match(s)
  if m_rev:
    rest = m_rev.group(1).strip()
    tok = re.split(r"[\s:]+", rest, maxsplit=1)[0]
    if tok:
      return _compact_review_target(tok, max_len=max_len)

  if len(s) <= max_len:
    return s

  anchored = _compact_with_anchor(s, max_len)
  if anchored:
    return anchored

  selected: list[str] = []
  cur_len = 0
  for w in s.split():
    w_clean = w.strip(".,;:!?")
    if not w_clean:
      continue
    add = len(w_clean) if not selected else len(w_clean) + 1
    if cur_len + add > max_len:
      break
    selected.append(w_clean)
    cur_len += add
  while selected and _clean_word_token(selected[-1]).lower() in _TITLE_STOPWORDS:
    selected.pop()
  if selected:
    return " ".join(selected)
  return s[: max_len - 1] + "…"


# --- Non-agent shell idle check ---


def normalize_shell_name(name: str) -> str:
  base = os.path.basename(name).lstrip("-")
  base = base.removesuffix("-wrapped")
  return base


def is_interactive_shell_proc(info: Mapping) -> bool:
  argv = info["argv"] or []
  if not argv:
    return normalize_shell_name(info["comm"]) in SHELL_NAMES
  shell = normalize_shell_name(argv[0])
  if shell not in SHELL_NAMES:
    return False
  # If invoked as `bash path/to/script`, argv has a non-flag positional script.
  for arg in argv[1:]:
    if arg in ("-c",):
      return False
    if not arg.startswith("-"):
      return False
  return True


def is_prompt_refresh_proc(info: Mapping) -> bool:
  """True for transient zsh prompt `git --no-optional-locks status` checks."""
  argv = info["argv"] or []
  if len(argv) >= 2 and os.path.basename(argv[0]) == "git":
    if "--no-optional-locks" in argv[1:]:
      return True
  return False


def busy_non_agent_processes(
    pane: Mapping,
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    self_ignored: set[int],
) -> list[str]:
  """Returns list of busy process descriptions in a non-agent tmux pane."""
  pane_pid = pane["pane_pid"]
  pane_info = procs.get(pane_pid)
  busy: list[str] = []
  seen_pids: set[int] = set()

  if pane_info and not is_interactive_shell_proc(pane_info):
    if pane_pid not in self_ignored:
      busy.append(f"{pane_info['comm']} (pid {pane_pid})")
      seen_pids.add(pane_pid)

  # Prune subtrees of self_ignored (except pane_pid itself) so children of
  # `code-harness snapshot` are never flagged.
  stack = list(children_map.get(pane_pid, ()))
  visited = {pane_pid}
  while stack:
    pid = stack.pop()
    if pid in visited:
      continue
    visited.add(pid)
    if pid in self_ignored:
      continue
    info = procs.get(pid)
    if not info or info["stat"].startswith("Z"):
      continue
    if is_prompt_refresh_proc(info):
      continue
    busy.append(f"{info['comm']} (pid {pid})")
    seen_pids.add(pid)
    stack.extend(children_map.get(pid, ()))

  # Also catch reparented processes still attached to this pane's tty.
  pane_rdev = 0
  if pane["pane_tty"]:
    try:
      pane_rdev = os.stat(pane["pane_tty"]).st_rdev
    except OSError:
      pane_rdev = 0
  tty_short = pane["pane_tty"].removeprefix("/dev/")
  for pid, info in procs.items():
    if (
        pid == pane_pid
        or pid in self_ignored
        or pid in seen_pids
        or info["stat"].startswith("Z")
        or is_prompt_refresh_proc(info)
    ):
      continue
    same_tty = (
        (pane_rdev and info["tty_nr"] == pane_rdev)
        or (not pane_rdev and tty_short and info["tty"] == tty_short)
    )
    if same_tty:
      busy.append(f"{info['comm']} (pid {pid})")
      seen_pids.add(pid)

  return busy


# --- Tmux scanning & snapshot generation ---


def scan_tmux(
    tmux: TmuxRunner,
    procs: Mapping[int, dict],
    children_map: Mapping[int, list[int]],
    data_dir: str | None = None,
    config: HarnessConfig | None = None,
) -> list[dict]:
  cfg = _get_config(config)
  sep = "\x1f"
  res = tmux.run(
      "list-sessions",
      "-F",
      sep.join([
          "#{session_name}",
          "#{session_attached}",
          "#{session_last_attached}",
      ]),
      check=False,
  )
  if res.returncode != 0:
    err = (res.stderr or "").strip()
    if (
        not err
        or "no server running" in err
        or "No such file or directory" in err
    ):
      return []
    raise RuntimeError(f"tmux list-sessions failed: {err}")
  if not res.stdout.strip():
    return []

  sessions_by_name: dict[str, dict] = {}
  session_order: list[str] = []
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 3:
      continue
    sname, attached, last_att = parts
    project_env = ""
    env_res = tmux.run(
        "show-environment", "-t", f"={sname}", "PROJECT", check=False
    )
    if env_res.returncode == 0:
      env_line = env_res.stdout.strip()
      if env_line.startswith("PROJECT="):
        project_env = env_line.split("=", 1)[1]
    sessions_by_name[sname] = {
        "name": sname,
        "attached": int(attached or "0") > 0,
        "last_attached": int(last_att or "0"),
        "project_env": project_env,
        "windows": {},
        "window_order": [],
    }
    session_order.append(sname)

  win_res = tmux.run(
      "list-windows",
      "-a",
      "-F",
      sep.join([
          "#{session_name}",
          "#{window_index}",
          "#{window_id}",
          "#{window_active}",
          "#{window_layout}",
          "#{window_name}",
          "#{automatic-rename}",
      ]),
  )
  for line in win_res.stdout.splitlines():
    parts = line.split(sep, 6)
    if len(parts) == 6:
      sname, widx_s, wid, wact, wlayout, wname = parts
      auto_s = "1"
    elif len(parts) == 7:
      sname, widx_s, wid, wact, wlayout, wname, auto_s = parts
    else:
      continue
    sess = sessions_by_name.get(sname)
    if not sess:
      continue
    widx = int(widx_s)
    sess["windows"][widx] = {
        "index": widx,
        "id": wid,
        "active": wact == "1",
        "layout": wlayout,
        "name": wname,
        "automatic_rename": auto_s == "1",
        "panes": [],
    }
    sess["window_order"].append(widx)

  pane_res = tmux.run(
      "list-panes",
      "-a",
      "-F",
      sep.join([
          "#{session_name}",
          "#{window_index}",
          "#{pane_index}",
          "#{pane_id}",
          "#{pane_pid}",
          "#{pane_tty}",
          "#{pane_active}",
          "#{pane_current_path}",
      ]),
  )
  mcp_sigs = load_mcp_commands(data_dir, config=cfg)
  self_ignored = self_ignored_pids(procs, children_map)

  for line in pane_res.stdout.splitlines():
    parts = line.split(sep, 7)
    if len(parts) != 8:
      continue
    sname, widx_s, pidx_s, pane_id, ppid_s, ptty, pact, pcwd = parts
    sess = sessions_by_name.get(sname)
    if not sess:
      continue
    win = sess["windows"].get(int(widx_s))
    if not win:
      continue
    pane = {
        "session_name": sname,
        "window_index": int(widx_s),
        "index": int(pidx_s),
        "pane_id": pane_id,
        "pane_pid": int(ppid_s),
        "pane_tty": ptty,
        "active": pact == "1",
        "cwd": pcwd or os.path.expanduser("~"),
        "agent": None,
        "busy_non_agent": [],
    }

    cli_proc, cli_is_print = find_pane_cli(
        pane["pane_pid"], procs, children_map, config=cfg
    )

    if cli_proc is not None:
      flavor, _ = cli_proc_flavor(cli_proc, config=cfg)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, pane_id, data_dir=data_dir, config=cfg
      )
      state, reason = classify_cli_state(
          cli_proc,
          cli_is_print,
          pane_id,
          conv_id,
          open_dbs,
          procs,
          children_map,
          mcp_sigs,
          self_ignored,
          cli_dir,
          config=cfg,
      )
      pane["agent"] = {
          "pid": cli_proc["pid"],
          "flavor": flavor or cfg.default_flavor,
          "data_dir": cli_dir,
          "conversation_id": conv_id,
          "state": state,
          "settled": state in CLI_SETTLED_STATES,
          "reason": reason,
          "label": conversation_label(cli_dir, conv_id, config=cfg),
      }
    else:
      pane["busy_non_agent"] = busy_non_agent_processes(
          pane, procs, children_map, self_ignored
      )

    win["panes"].append(pane)

  result = []
  for sname in session_order:
    sess = sessions_by_name[sname]
    sess["window_order"].sort()
    for widx in sess["window_order"]:
      sess["windows"][widx]["panes"].sort(key=lambda p: p["index"])
    result.append(sess)
  return result


def short_home(path: str) -> str:
  home = os.path.expanduser("~")
  if path == home:
    return "~"
  if path.startswith(home + "/"):
    return "~" + path[len(home) :]
  return path


def _shell_ident(name: str) -> str:
  cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
  if not cleaned or cleaned[0].isdigit():
    cleaned = f"f_{cleaned}"
  return cleaned


def _flavor_shell_vars(
    flavors_used: list[str],
    config: HarnessConfig,
) -> dict[str, tuple[str, str]]:
  """Maps each flavor name to `(env_var_name, quoted_shell_var_name)`."""
  mapping: dict[str, tuple[str, str]] = {}
  used_env: set[str] = set()
  used_q: set[str] = set()
  for fname in flavors_used:
    ident = _shell_ident(fname)
    flavor_cfg = config.flavors.get(fname)
    env_var = (
        flavor_cfg.env_bin
        if flavor_cfg and flavor_cfg.env_bin
        else f"CODE_HARNESS_CLI_{ident.upper()}"
    )
    base_env = env_var
    suffix = 2
    while env_var in used_env:
      env_var = f"{base_env}_{suffix}"
      suffix += 1
    used_env.add(env_var)

    q_var = f"cli_{ident.lower()}_q"
    base_q = q_var
    suffix = 2
    while q_var in used_q:
      q_var = f"{base_q}_{suffix}"
      suffix += 1
    used_q.add(q_var)

    mapping[fname] = (env_var, q_var)
  return mapping


def render_restore_script(
    sessions: list[dict],
    config: HarnessConfig | None,
    recorded_at: str,
    cli_bins: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  """Generates an executable bash script that recreates tmux + CLI sessions."""
  cfg = _get_config(config)
  agent_panes = [
      p
      for s in sessions
      for widx in s["window_order"]
      for p in s["windows"][widx]["panes"]
      if p["agent"] is not None
  ]

  lines = [
      "#!/usr/bin/env bash",
      f"# code-harness tmux snapshot recorded at {recorded_at}",
      "#",
      f"# Conversations ({len(agent_panes)}):",
  ]
  for p in agent_panes:
    ag = p["agent"]
    loc = f"{p['session_name']}:{p['window_index']}.{p['index']}"
    cid = ag["conversation_id"] or "(new)"
    state = "settled" if ag["settled"] else f"active: {ag['reason']}"
    label = f"  {ag['label']}" if ag["label"] else ""
    lines.append(f"#   {loc}  {short_home(p['cwd'])}")
    lines.append(f"#     {cid}  [{state}]{label}")

  present_flavors = {
      p["agent"].get("flavor", cfg.default_flavor) for p in agent_panes
  }
  flavors_used: list[str] = [
      flv.name for flv in ordered_flavors(cfg) if flv.name in present_flavors
  ]
  for fname in sorted(present_flavors):
    if fname not in flavors_used:
      flavors_used.append(fname)
  if not flavors_used:
    flavors_used = [cfg.default_flavor]

  flavor_vars = _flavor_shell_vars(flavors_used, cfg)
  use_global_env = len(flavors_used) == 1

  lines.extend([
      "#",
      "# Restore with:",
      "#   code-harness snapshot --restore",
      "",
      "set -euo pipefail",
      "",
  ])
  for fname in flavors_used:
    env_var, q_var = flavor_vars[fname]
    if cli_bins and fname in cli_bins:
      resolved_bin = cli_bins[fname]
    else:
      resolved_bin = find_cli_bin(
          cfg, flavor=fname, env=env, use_global_env=use_global_env
      )
    lines.extend([
        f"{env_var}=${{{env_var}:-{shlex.quote(resolved_bin)}}}",
        f"printf -v {q_var} '%q' \"${env_var}\"",
    ])

  lines.extend([
      "tmux_cmd=(tmux)",
      'if [[ -n "${CODE_HARNESS_TMUX_SOCKET:-}" ]]; then',
      '  tmux_cmd=(tmux -S "$CODE_HARNESS_TMUX_SOCKET")',
      "fi",
      "",
      "safe_dir() {",
      '  if [[ -d "$1" ]]; then',
      '    printf "%s\\n" "$1"',
      "  else",
      '    printf "%s\\n" "$HOME"',
      "  fi",
      "}",
      "",
  ])

  # Pick session to attach to at the end: attached / most recently attached.
  focus_session = ""
  if sessions:
    ranked = sorted(
        sessions,
        key=lambda s: (1 if s["attached"] else 0, s["last_attached"]),
        reverse=True,
    )
    focus_session = ranked[0]["name"]

  for sess in sessions:
    sname = sess["name"]
    q_sname = shlex.quote(sname)
    q_exact_s = shlex.quote(f"={sname}")
    w_indices = sess["window_order"]
    if not w_indices:
      continue

    lines.append(f"# --- Session {sname} ---")
    lines.append(
        f'if "${{tmux_cmd[@]}}" has-session -t {q_exact_s} 2>/dev/null; then'
    )
    lines.append(
        f"  printf 'code-harness snapshot: session %s already exists,"
        f" skipping\\n' {q_sname} >&2"
    )
    lines.append("else")

    active_win_idx = w_indices[0]
    for pos, widx in enumerate(w_indices):
      win = sess["windows"][widx]
      if win["active"]:
        active_win_idx = widx
      panes = win["panes"]
      if not panes:
        continue
      p0 = panes[0]
      q_wname = shlex.quote(win["name"])
      name_opt = "" if win.get("automatic_rename", True) else f" -n {q_wname}"
      q_p0_cwd = shlex.quote(p0["cwd"])

      if pos == 0:
        env_part = ""
        if sess["project_env"]:
          env_part = f" -e {shlex.quote('PROJECT=' + sess['project_env'])}"
        lines.append(f"  d0=$(safe_dir {q_p0_cwd})")
        lines.append(
            f'  read -r cur_win cur_idx pane_0 < <(TMUX="" "${{tmux_cmd[@]}}"'
            f" -2 new-session -d -P"
            " -F '#{window_id} #{window_index} #{pane_id}'"
            f' -s {q_sname}{name_opt} -c "$d0"{env_part})'
        )
        lines.append(f'  if [[ "$cur_idx" != "{widx}" ]]; then')
        lines.append(
            f'    "${{tmux_cmd[@]}}" move-window -s "$cur_win"'
            f" -t {shlex.quote(f'={sname}:{widx}')}"
        )
        lines.append("  fi")
      else:
        lines.append(f"  d0=$(safe_dir {q_p0_cwd})")
        lines.append(
            f'  read -r cur_win pane_0 < <("${{tmux_cmd[@]}}" new-window -d -P'
            " -F '#{window_id} #{pane_id}'"
            f' -t {shlex.quote(f"={sname}:{widx}")}{name_opt} -c "$d0")'
        )

      active_pane_var = "$pane_0"
      for p_pos, pane in enumerate(panes[1:], start=1):
        q_pcwd = shlex.quote(pane["cwd"])
        lines.append(f"  dp=$(safe_dir {q_pcwd})")
        lines.append(
            f"  pane_{p_pos}=$("
            f'"${{tmux_cmd[@]}}" split-window -d -P -F \'#{{pane_id}}\''
            f' -t "$cur_win" -c "$dp")'
        )
        if pane["active"]:
          active_pane_var = f"$pane_{p_pos}"

      if len(panes) > 1 and win["layout"]:
        lines.append(
            f'  "${{tmux_cmd[@]}}" select-layout -t "$cur_win"'
            f" {shlex.quote(win['layout'])} >/dev/null 2>&1 || true"
        )
      lines.append(
          f'  "${{tmux_cmd[@]}}" select-pane -t "{active_pane_var}"'
      )

      for p_pos, pane in enumerate(panes):
        ag = pane["agent"]
        if ag is None:
          continue
        cid = ag["conversation_id"]
        fname = ag.get("flavor", cfg.default_flavor)
        _, q_var = flavor_vars[fname]
        cmd_var = f"${q_var}"
        if cid:
          # `cid` is validated against UUID_RE (hex + hyphens only).
          cmd_str = f" {cmd_var} --conversation={cid}"
        else:
          cmd_str = f" {cmd_var}"
        lines.append(
            f'  "${{tmux_cmd[@]}}" send-keys -t "$pane_{p_pos}" -l'
            f' "{cmd_str}"'
        )
        lines.append(
            f'  "${{tmux_cmd[@]}}" send-keys -t "$pane_{p_pos}" Enter'
        )

    lines.append(
        f'  "${{tmux_cmd[@]}}" select-window'
        f" -t {shlex.quote(f'={sname}:{active_win_idx}')}"
    )
    lines.append("fi")
    lines.append("")

  if focus_session:
    q_focus_exact = shlex.quote(f"={focus_session}")
    lines.extend([
        'if [[ "${CODE_HARNESS_NO_ATTACH:-0}" != "1" ]] && '
        f'"${{tmux_cmd[@]}}" has-session -t {q_focus_exact} 2>/dev/null; then',
        '  if [[ -n "${TMUX:-}" ]]; then',
        f'    "${{tmux_cmd[@]}}" switch-client -t {q_focus_exact}',
        "  elif [[ -t 0 && -t 1 ]]; then",
        f'    exec "${{tmux_cmd[@]}}" -2 attach-session -t {q_focus_exact}',
        "  fi",
        "fi",
    ])

  return "\n".join(lines) + "\n"


def save_snapshot(
    script_text: str, output_path: str | None = None
) -> tuple[str, str | None]:
  if output_path:
    target = os.path.abspath(os.path.expanduser(output_path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
      f.write(script_text)
    os.chmod(tmp, 0o755)
    os.replace(tmp, target)
    return target, None

  state_dir = default_state_dir()
  os.makedirs(state_dir, exist_ok=True)
  stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
  snap_path = os.path.join(state_dir, f"snapshot-{stamp}.sh")
  tmp = f"{snap_path}.tmp.{os.getpid()}"
  with open(tmp, "w", encoding="utf-8") as f:
    f.write(script_text)
  os.chmod(tmp, 0o755)
  os.replace(tmp, snap_path)

  latest = os.path.join(state_dir, "latest.sh")
  latest_tmp = f"{latest}.tmp.{os.getpid()}"
  try:
    os.unlink(latest_tmp)
  except FileNotFoundError:
    pass
  os.symlink(os.path.basename(snap_path), latest_tmp)
  os.replace(latest_tmp, latest)
  return latest, snap_path


def prompt_confirm(prompt_text: str) -> bool:
  try:
    with open("/dev/tty", "r+", encoding="utf-8") as tty:
      tty.write(f"{prompt_text} [y/N] ")
      tty.flush()
      ans = tty.readline()
  except OSError:
    return False
  return ans.strip().lower().startswith("y")


def wait_for_pids_exit(pids: list[int], timeout_sec: float = 5.0) -> set[int]:
  deadline = time.monotonic() + timeout_sec
  remaining = set(pids)
  while remaining and time.monotonic() < deadline:
    still_alive = set()
    for pid in remaining:
      try:
        os.kill(pid, 0)
        still_alive.add(pid)
      except ProcessLookupError:
        pass
      except PermissionError:
        still_alive.add(pid)
    remaining = still_alive
    if remaining:
      time.sleep(0.1)
  return remaining


def print_scan_report(
    sessions: list[dict],
) -> tuple[list[dict], list[dict]]:
  agent_panes: list[dict] = []
  busy_panes: list[dict] = []
  for sess in sessions:
    for widx in sess["window_order"]:
      for pane in sess["windows"][widx]["panes"]:
        if pane["agent"] is not None:
          agent_panes.append(pane)
        elif pane["busy_non_agent"]:
          busy_panes.append(pane)

  if not agent_panes:
    print("No open coding-agent CLI instances found in tmux.")
  else:
    print(f"Found {len(agent_panes)} coding-agent CLI instance(s):")
    for p in agent_panes:
      ag = p["agent"]
      loc = f"{p['session_name']}:{p['window_index']}.{p['index']}"
      cid = ag["conversation_id"] or "(new prompt)"
      tag = "settled" if ag["settled"] else f"ACTIVE ({ag['reason']})"
      label = f" - {ag['label']}" if ag["label"] else ""
      print(f"  {loc:<18} {tag:<24} {cid}")
      print(f"    {short_home(p['cwd'])}{label}")

  if busy_panes:
    print(f"\nNon-agent panes with running processes ({len(busy_panes)}):")
    for p in busy_panes:
      loc = f"{p['session_name']}:{p['window_index']}.{p['index']}"
      procs_s = ", ".join(p["busy_non_agent"])
      print(f"  {loc:<18} {procs_s}  ({short_home(p['cwd'])})")

  return agent_panes, busy_panes


def run_restore(args: argparse.Namespace, tmux_bin: str) -> int:
  path = args.restore_file or os.path.join(default_state_dir(), "latest.sh")
  path = os.path.expanduser(path)
  if not os.path.isfile(path):
    print(
        f"code-harness snapshot: no snapshot file found at {path}",
        file=sys.stderr,
    )
    return 1
  env = os.environ.copy()
  # Put the directory of the chosen tmux first on PATH so `tmux` in script
  # matches the server version.
  tmux_dir = os.path.dirname(tmux_bin)
  if tmux_dir:
    env["PATH"] = f"{tmux_dir}:{env.get('PATH', '')}"
  if args.tmux_socket:
    env["CODE_HARNESS_TMUX_SOCKET"] = args.tmux_socket
  if args.no_attach:
    env["CODE_HARNESS_NO_ATTACH"] = "1"
  res = subprocess.run(["bash", path], env=env, check=False)
  return res.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness snapshot",
      description=(
          "Scan active tmux sessions for coding-agent CLI conversations, "
          "record an executable layout/conversation restore script, and close "
          "cleanly."
      ),
  )
  p.add_argument(
      "-n",
      "--dry-run",
      "--status",
      dest="dry_run",
      action="store_true",
      help="Scan and report status without writing a snapshot or closing.",
  )
  p.add_argument(
      "--snapshot-only",
      action="store_true",
      help="Write the restore snapshot script without closing CLI or tmux.",
  )
  p.add_argument(
      "-r",
      "--restore",
      nargs="?",
      const="",
      default=None,
      metavar="FILE",
      dest="restore_file",
      help="Restore tmux layout and agent CLIs from FILE (default: latest).",
  )
  p.add_argument(
      "--no-attach",
      action="store_true",
      help="With --restore, recreate sessions detached without attaching.",
  )
  p.add_argument(
      "--keep-tmux",
      action="store_true",
      help="Close coding-agent CLI processes cleanly, leaving tmux running.",
  )
  p.add_argument(
      "--all-sessions",
      action="store_true",
      help="Include tmux sessions that have no open agent CLI in snapshot.",
  )
  p.add_argument(
      "-f",
      "--force",
      action="store_true",
      help="Allow closing even when non-agent panes run vim or other jobs.",
  )
  p.add_argument(
      "-y",
      "--yes",
      action="store_true",
      help="Skip confirmation prompt when agent conversations are active.",
  )
  p.add_argument(
      "-o",
      "--output",
      metavar="FILE",
      help="Write snapshot script to FILE instead of default state directory.",
  )
  p.add_argument(
      "--tmux-socket",
      metavar="PATH",
      help="Connect to tmux socket at PATH (tmux -S PATH).",
  )
  return p.parse_args(argv)


def cmd_snapshot(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  tmux_bin = find_tmux_bin()

  if args.restore_file is not None:
    return run_restore(args, tmux_bin)

  tmux = TmuxRunner(tmux_bin, args.tmux_socket)
  procs, children_map = load_process_table(config=config)
  data_dir = default_data_dir(config=config)
  sessions = scan_tmux(tmux, procs, children_map, data_dir, config=config)

  if not sessions:
    print("No active tmux sessions found.")
    return 0

  agent_panes, busy_panes = print_scan_report(sessions)

  if args.dry_run:
    active_count = sum(1 for p in agent_panes if not p["agent"]["settled"])
    if active_count or busy_panes:
      return 1
    return 0

  if args.all_sessions:
    snap_sessions = sessions
  else:
    snap_sessions = [
        s
        for s in sessions
        if any(
            p["agent"] is not None
            for widx in s["window_order"]
            for p in s["windows"][widx]["panes"]
        )
    ]

  if not snap_sessions:
    print("Nothing to snapshot (use --all-sessions to include plain sessions).")
    return 0

  if not args.snapshot_only and busy_panes and not args.force:
    if args.keep_tmux:
      print(
          "\nNote: leaving non-agent panes untouched (--keep-tmux).",
          file=sys.stderr,
      )
    else:
      print(
          "\nRefusing to close tmux while non-agent panes are running "
          "processes.\nFinish those jobs first, pass --keep-tmux to close "
          "only agent CLIs, or pass --force.",
          file=sys.stderr,
      )
      return 1

  active_agents = [p for p in agent_panes if not p["agent"]["settled"]]
  if not args.snapshot_only and active_agents and not args.yes:
    print(
        "\nWarning: The coding-agent CLI runs an in-process agent server.\n"
        "Closing an active CLI aborts its in-flight turn, subagents, and "
        "background tasks.",
        file=sys.stderr,
    )
    if not prompt_confirm(
        f"Close {len(active_agents)} active conversation(s) anyway?"
    ):
      print("Aborted.", file=sys.stderr)
      return 1

  recorded_at = (
      datetime.datetime.now(datetime.timezone.utc)
      .replace(microsecond=0)
      .isoformat()
      .replace("+00:00", "Z")
  )
  script_text = render_restore_script(snap_sessions, config, recorded_at)
  latest_path, archive_path = save_snapshot(script_text, args.output)
  display_path = short_home(latest_path)
  if archive_path:
    print(
        f"\nSaved snapshot: {display_path} ({os.path.basename(archive_path)})"
    )
  else:
    print(f"\nSaved snapshot: {display_path}")

  if args.snapshot_only:
    return 0

  cli_pids = [p["agent"]["pid"] for p in agent_panes]
  if args.keep_tmux:
    if cli_pids:
      print(f"Closing {len(cli_pids)} coding-agent CLI instance(s)...")
      for pid in cli_pids:
        try:
          os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
          pass
      leftover = wait_for_pids_exit(cli_pids, timeout_sec=5.0)
      if leftover:
        print(
            f"Warning: {len(leftover)} CLI process(es) still shutting down: "
            f"{sorted(leftover)}",
            file=sys.stderr,
        )
    return 0

  # Collect attached client ttys before closing so we can echo the restore hint
  # after tmux exits.
  client_ttys = []
  c_res = tmux.run("list-clients", "-F", "#{client_tty}", check=False)
  if c_res.returncode == 0:
    client_ttys = [t.strip() for t in c_res.stdout.splitlines() if t.strip()]

  if cli_pids:
    print(f"Closing {len(cli_pids)} coding-agent CLI instance(s) and tmux...")
  else:
    print("Closing tmux...")
  sys.stdout.flush()
  sys.stderr.flush()

  # Run shutdown in a detached session (`setsid` + `nohup` equivalent) so
  # tearing down our own tmux pane/shell cannot abort WAL flush or kill-server.
  signal.signal(signal.SIGHUP, signal.SIG_IGN)
  child_pid = os.fork()
  if child_pid > 0:
    try:
      os.waitpid(child_pid, 0)
    except (OSError, KeyboardInterrupt):
      pass
    return 0

  try:
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
      try:
        os.dup2(devnull, fd)
      except OSError:
        pass
    if devnull > 2:
      os.close(devnull)

    for pid in cli_pids:
      try:
        os.kill(pid, signal.SIGTERM)
      except ProcessLookupError:
        pass
    wait_for_pids_exit(cli_pids, timeout_sec=5.0)

    tmux.run("kill-server", check=False)
    time.sleep(0.05)
    msg = (
        f"Saved {len(agent_panes)} conversation(s) to {display_path}\n"
        "Restore with: code-harness snapshot --restore\n"
    )
    for ctty in client_ttys:
      try:
        with open(ctty, "w", encoding="utf-8") as f:
          f.write(msg)
      except OSError:
        pass
  finally:
    os._exit(0)

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

SHELL_NAMES = frozenset(
    {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"}
)

# Trajectory SQLite protobuf field & enum constants.
META_PARENT = 5
META_ROOT = 6

STEP_TYPE_FINISH = 2
STEP_TYPE_USER_INPUT = 14
STEP_TYPE_CHECKPOINT = 23
STEP_TYPE_SYSTEM_MESSAGE = 101
STEP_TYPE_GENERIC = 132

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

STEP_GENERIC = 140
GENERIC_RESULT = 2
GENERIC_STEP_RENDER_INFO = 3
RENDER_INFO_CONTENT_BLOCKS = 10
CONTENT_BLOCK_AGENT = 4
AGENT_REF_CONVERSATION_ID = 1


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


def _load_linux_proc_table() -> dict[int, dict]:
  """Reads `/proc` directly without spawning a `ps` child process."""
  btime = _linux_boot_time()
  try:
    clk_tck = os.sysconf("SC_CLK_TCK")
  except (AttributeError, ValueError, OSError):
    clk_tck = 100

  procs: dict[int, dict] = {}
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
    if not argv:
      argv = [comm]
    procs[pid] = {
        "pid": pid,
        "ppid": ppid,
        "stat": stat_ch,
        "tty": "",
        "tty_nr": tty_nr,
        "comm": comm,
        "argv": argv,
        "args_str": " ".join(argv),
        "exe": exe,
        "start_epoch": start_epoch,
    }
  return procs


def load_process_table(
    config: HarnessConfig | None = None,
) -> tuple[dict[int, dict], dict[int, list[int]]]:
  """Returns ({pid: proc_dict}, {ppid: [child_pids]})."""
  global _PRELOADED_OPEN_FILES
  cfg = _get_config(config)
  if os.path.isdir("/proc"):
    _PRELOADED_OPEN_FILES = None
    procs = _load_linux_proc_table()
  else:
    now = time.time()
    res = subprocess.run(
        ["ps", "-ww", "-eo", "pid=,ppid=,stat=,tty=,etime=,args="],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    procs = {}
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
  """True if `info` is an invocation of `code-harness snapshot` itself."""
  argv = info.get("argv") or ()
  has_cmd = any(
      os.path.basename(tok) in ("code-harness", ".code-harness-wrapped")
      for tok in argv[:3]
  )
  if has_cmd and "snapshot" in argv:
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
    return lock_ids[-1], open_dbs, cli_dir

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


def _inspect_settled_state(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[bool, str, str, bool, bool, tuple[str, ...]]:
  """Returns (settled_bool, reason, last_ts, has_db, was_canceled, wf_ids)."""
  has_db = False
  eff_sql = None
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
        return True, "empty conversation", "", True, False, ()
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
      raw_rows = con.execute(
          f"SELECT idx, step_type, status, {td_col}, {pl_col} FROM steps "
          "WHERE idx >= ? AND status IN (?, ?, ?, ?, ?) ORDER BY idx ASC",
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
      for row_idx, st_type, st_val, task_blob, payload in raw_rows:
        keep = False
        if st_val == STATUS_WAITING:
          keep = row_idx >= ckpt_idx
        elif task_blob:
          keep = row_idx >= ckpt_idx and not _is_daemon_task(task_blob)
        else:
          keep = row_idx >= min_idx
        if not keep:
          continue
        kept.append((row_idx, st_type, st_val, task_blob))
        if st_type == STEP_TYPE_GENERIC and payload:
          for cid in _workflow_agent_ids_from_step(payload):
            if cid not in wf_seen:
              wf_seen.add(cid)
              wf_ids.append(cid)
      if kept:
        if any(row[2] == STATUS_WAITING for row in kept):
          return (
              False,
              "waiting on permission check or question",
              "",
              True,
              False,
              tuple(wf_ids),
          )
        if kept[-1][2] == STATUS_GENERATING:
          return (
              False,
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
          return False, msg, "", True, False, tuple(wf_ids)
        return False, "step currently running", "", True, False, tuple(wf_ids)

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
    except sqlite3.Error:
      has_db = False
    finally:
      con.close()

  entries = read_transcript_entries(data_dir, conv_id, config=config)
  last, last_ts = _trailing_transcript_entry_and_ts(entries)
  if eff_sql and eff_sql[1] in (STATUS_CANCELED, STATUS_INTERRUPTED):
    return True, "waiting for user input", last_ts, has_db, True, tuple(wf_ids)

  if not entries or last is None:
    if eff_sql and eff_sql[0] == STEP_TYPE_USER_INPUT:
      return False, "turn starting", "", has_db, False, tuple(wf_ids)
    return True, "waiting for user input", "", has_db, False, tuple(wf_ids)

  st = last.get("status") or ""
  tp = last.get("type") or ""
  if st in ("PENDING", "RUNNING", "WAITING"):
    return (
        False,
        f"{tp.lower()} is {st.lower()}",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )
  if st == "CANCELED":
    return True, "waiting for user input", last_ts, has_db, True, tuple(wf_ids)
  if tp == "PLANNER_RESPONSE":
    if last.get("tool_calls"):
      return (
          False,
          "waiting on tool call or confirmation",
          last_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return True, "waiting for user input", last_ts, has_db, False, tuple(wf_ids)
  if tp == "ERROR_MESSAGE":
    if _is_recent_iso_timestamp(last.get("created_at") or ""):
      return (
          False,
          "model error/retry in progress",
          last_ts,
          has_db,
          False,
          tuple(wf_ids),
      )
    return (
        True,
        "waiting for user input (after error)",
        last_ts,
        has_db,
        False,
        tuple(wf_ids),
    )

  return (
      False,
      f"turn in progress after {tp.lower()}",
      last_ts,
      has_db,
      False,
      tuple(wf_ids),
  )


def check_db_and_transcript_settled(
    data_dir: str | None,
    conv_id: str,
    config: HarnessConfig | None = None,
) -> tuple[bool, str, str, list[dict]]:
  """Returns (settled_bool, reason, last_created_at, entries)."""
  settled, reason, last_ts, _, _, _ = _inspect_settled_state(
      data_dir, conv_id, config=config
  )
  entries = read_transcript_entries(data_dir, conv_id, config=config)
  return settled, reason, last_ts, entries


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
  if is_print:
    return False, "headless --print run in progress"

  cli_start_epoch = cli_info.get("start_epoch")
  min_epoch = (cli_start_epoch - 2.0) if cli_start_epoch is not None else None

  if conv_id is None:
    kids = active_cli_child_processes(
        cli_info["pid"], procs, children_map, mcp_sigs, self_ignored
    )
    if kids:
      sample = kids[0]["comm"]
      return False, f"active child task ({sample}, pid {kids[0]['pid']})"
    return True, "empty prompt (no conversation yet)"

  settled, reason, parent_ts, has_db, parent_canceled, wf_ids = (
      _inspect_settled_state(data_dir, conv_id, config=config)
  )

  saw_sub_active = None
  for sub_id, sub_label in discover_active_child_agents(
      data_dir,
      conv_id,
      open_dbs,
      cli_start_epoch=cli_start_epoch,
      extra_seed_ids=wf_ids,
      config=config,
  ):
    sub_settled, sub_reason, sub_ts, _, _, _ = _inspect_settled_state(
        data_dir, sub_id, config=config
    )
    if not sub_settled:
      saw_sub_active = f"subagent {sub_label} active ({sub_reason})"
      break
    if has_wakeable_undelivered_message(
        data_dir, sub_id, min_epoch=min_epoch, config=config
    ):
      saw_sub_active = f"subagent {sub_label} wakeup pending"
      break
    if (
        settled
        and not parent_canceled
        and _iso_gt(sub_ts, parent_ts)
        and _is_recent_iso_timestamp(sub_ts, max_age_sec=5.0)
    ):
      saw_sub_active = f"subagent {sub_label} wakeup pending"
      break

  if not settled:
    return False, reason
  if saw_sub_active is not None:
    return False, saw_sub_active

  kids = active_cli_child_processes(
      cli_info["pid"], procs, children_map, mcp_sigs, self_ignored
  )
  if kids:
    sample = kids[0]["comm"]
    return False, f"active child task ({sample}, pid {kids[0]['pid']})"

  # Optional real-time statusLine state file fallback / extra task check.
  pane_state_path = _pane_state_path(pane_id)
  if os.path.isfile(pane_state_path):
    try:
      with open(pane_state_path, encoding="utf-8") as f:
        st = json.load(f)
      if st.get("conversation_id") == conv_id:
        ag = st.get("agent_state") or ""
        if not has_db:
          if ag == "working":
            return False, "agent actively working"
          if ag == "tool_use" or st.get("tool_confirmation_pending"):
            return False, "waiting on tool confirmation or question"
        if (st.get("task_count") or 0) > 0:
          return False, f"{st['task_count']} background task(s) active"
        for sub in st.get("subagents") or []:
          if sub.get("status") == "running":
            return False, f"subagent running ({sub.get('role') or 'subagent'})"
        if (st.get("pending_input_count") or 0) > 0:
          return False, "queued input pending"
    except (OSError, ValueError):
      pass

  if not parent_canceled and has_wakeable_undelivered_message(
      data_dir, conv_id, min_epoch=min_epoch, config=config
  ):
    return False, "wakeup message pending"

  return True, reason


def conversation_label(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> str:
  """Short human-readable summary/title for `conv_id`, if available."""
  if not conv_id:
    return "(new prompt)"

  # Try conversation_summaries.db if accessible.
  for d in harness_data_dirs(data_dir, config=config):
    sum_db = os.path.join(d, "conversation_summaries.db")
    if not os.path.isfile(sum_db):
      continue
    try:
      uri = pathlib.Path(sum_db).as_uri() + "?mode=ro"
      con = sqlite3.connect(uri, uri=True, timeout=0.5)
      try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for tbl in tables:
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
          if id_col and title_col:
            row = con.execute(
                f"SELECT {title_col} FROM {tbl} WHERE {id_col} = ? LIMIT 1",
                (conv_id,),
            ).fetchone()
            if row and row[0] and str(row[0]).strip():
              return _squash_line(str(row[0]), 64)
      finally:
        con.close()
    except sqlite3.Error:
      pass

  # Fallback: first USER_INPUT in transcript.jsonl.
  for entry in read_transcript_entries(data_dir, conv_id, config=config):
    if entry.get("type") == "USER_INPUT":
      text = entry.get("content") or ""
      m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.S)
      if m:
        text = m.group(1)
      if text.strip():
        return _squash_line(text, 64)
      break
  return ""


def _squash_line(text: str, limit: int) -> str:
  single = " ".join(text.split())
  if len(single) <= limit:
    return single
  return single[: limit - 3] + "..."


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
      ]),
  )
  for line in win_res.stdout.splitlines():
    parts = line.split(sep, 5)
    if len(parts) != 6:
      continue
    sname, widx_s, wid, wact, wlayout, wname = parts
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

    # Find topmost CLI process in this pane (pane_pid or descendant).
    cli_proc = None
    cli_is_print = False
    root_info = procs.get(pane["pane_pid"])
    if root_info:
      ok, is_pr = is_cli_proc(root_info, config=cfg)
      if ok:
        cli_proc, cli_is_print = root_info, is_pr
    if cli_proc is None:
      # Breadth/top-down check so we pick the parent CLI, not any child CLI.
      queue = list(children_map.get(pane["pane_pid"], ()))
      visited = {pane["pane_pid"]}
      while queue:
        cpid = queue.pop(0)
        if cpid in visited:
          continue
        visited.add(cpid)
        cinfo = procs.get(cpid)
        if not cinfo:
          continue
        ok, is_pr = is_cli_proc(cinfo, config=cfg)
        if ok:
          cli_proc, cli_is_print = cinfo, is_pr
          break
        queue.extend(children_map.get(cpid, ()))

    if cli_proc is not None:
      flavor, _ = cli_proc_flavor(cli_proc, config=cfg)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, pane_id, data_dir=data_dir, config=cfg
      )
      settled, reason = classify_cli_instance(
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
          "settled": settled,
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
            f' -s {q_sname} -n {q_wname} -c "$d0"{env_part})'
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
            f' -t {shlex.quote(f"={sname}:{widx}")} -n {q_wname} -c "$d0")'
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

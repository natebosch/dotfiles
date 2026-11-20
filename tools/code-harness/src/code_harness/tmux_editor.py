"""Two-stage tmux vertical-split editor wrapper for coding-agent CLIs.

For prompt temp files (matching configured flavor `prompt_file_globs`, e.g.
`agy-prompt-*.txt`) inside tmux, Pass 1 opens a non-blocking side-by-side split
running Vim on a draft copy and returns immediately so the agent TUI stays
live. When Vim saves and exits, a detached apply worker waits for the split to
close and sends `C-g` to the original pane so Pass 2 supplies the saved prompt
back to the CLI.

For all other files inside tmux (e.g. `keybindings.json` or artifacts), opens
Vim in a blocking vertical split and propagates its exit code. Outside tmux,
execs Vim directly.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Mapping, Sequence
import uuid

from code_harness.config import (
    HarnessConfig,
    load_config,
    ordered_flavors,
)
from code_harness.snapshot import (
    TmuxRunner,
    classify_cli_state,
    default_data_dir,
    find_pane_cli,
    find_tmux_bin,
    is_urgent_cli_state,
    load_mcp_commands,
    load_process_table,
    resolve_cli_conversation_and_dir,
    self_ignored_pids,
)
from code_harness.tmux_next import show_toast as _tmux_show_toast

NESTED_SELF_NAMES = frozenset({"edit_nested", "code-harness-tmux-editor"})
HARNESS_BIN_NAMES = frozenset({"code-harness", ".code-harness-wrapped"})

FORWARD_ENV_VARS = (
    "PATH",
    "HOME",
    "COLORTERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "PYTHONPATH",
    "PROJECT",
    "VISUAL",
    "EDITOR",
    "CODE_HARNESS_CONFIG",
    "CODE_HARNESS_FLAVOR",
    "CODE_HARNESS_NO_PLUGINS",
    "CODE_HARNESS_BIN",
    "CODE_HARNESS_DATA_DIR",
    "CODE_HARNESS_TMUX_EDITOR_VIM",
    "CODE_HARNESS_TMUX_EDITOR_STATE_DIR",
    "CODE_HARNESS_TMUX_EDITOR_SPLIT_SIZE",
    "CODE_HARNESS_TMUX_EDITOR_FORCE_SEND",
    "CODE_HARNESS_TMUX_EDITOR_SKIP_PROC_CHECK",
    "CODE_HARNESS_TMUX_EDITOR_PROMPT_GLOB",
    "CODE_HARNESS_TMUX_TOAST_LOG",
    "CODE_HARNESS_TMUX_SYNC_TOAST",
    "AGY_CLI_BIN",
)


class _SignalExit(Exception):
  """Raised when a worker receives SIGHUP or SIGTERM."""


def tmux_base_cmd(socket_path: str = "") -> list[str]:
  tmux_bin = find_tmux_bin()
  if socket_path:
    return [tmux_bin, "-S", socket_path]
  return [tmux_bin]


def run_tmux(
    tmux_base: Sequence[str], *args: str, check: bool = False
) -> subprocess.CompletedProcess:
  return subprocess.run(
      [*tmux_base, *args],
      stdin=subprocess.DEVNULL,
      capture_output=True,
      text=True,
      check=check,
  )


def load_tmux_buffer(
    tmux_base: Sequence[str], data: bytes
) -> subprocess.CompletedProcess:
  return subprocess.run(
      [*tmux_base, "load-buffer", "-"],
      input=data,
      capture_output=True,
      check=False,
  )


def _self_editor_cmd(flavor: str | None = None) -> list[str]:
  argv0 = sys.argv[0] if sys.argv else ""
  base0 = os.path.basename(argv0)
  cmd: list[str]
  if (
      base0 == "code-harness"
      and os.path.isfile(argv0)
      and os.access(argv0, os.X_OK)
  ):
    cmd = [os.path.realpath(argv0)]
  elif base0 == ".code-harness-wrapped":
    sibling = os.path.join(
        os.path.dirname(os.path.abspath(argv0)), "code-harness"
    )
    if os.path.isfile(sibling) and os.access(sibling, os.X_OK):
      cmd = [sibling]
    else:
      cmd = [sys.executable, "-m", "code_harness.cli"]
  else:
    cmd = [sys.executable, "-m", "code_harness.cli"]

  if flavor:
    cmd.extend(["--flavor", flavor])
  cmd.append("tmux-editor")
  return cmd


def _is_self_or_nested(parts: Sequence[str]) -> bool:
  if not parts or not parts[0]:
    return True
  cmd0 = parts[0]
  base0 = os.path.basename(cmd0)
  if base0 in NESTED_SELF_NAMES:
    return True
  if base0 in HARNESS_BIN_NAMES and "tmux-editor" in parts[1:]:
    return True
  if (
      len(parts) >= 4
      and parts[1] == "-m"
      and parts[2] in ("code_harness", "code_harness.cli")
      and "tmux-editor" in parts[3:]
  ):
    return True

  resolved = shutil.which(cmd0) or (cmd0 if os.path.sep in cmd0 else "")
  if resolved:
    try:
      real = os.path.realpath(resolved)
    except OSError:
      real = resolved
    real_base = os.path.basename(real)
    if real_base in NESTED_SELF_NAMES:
      return True
    if real_base in HARNESS_BIN_NAMES and "tmux-editor" in parts[1:]:
      return True
  return False


def resolve_vim_command(env: Mapping[str, str] | None = None) -> list[str]:
  env_map = os.environ if env is None else env
  override = (env_map.get("CODE_HARNESS_TMUX_EDITOR_VIM") or "").strip()
  if override:
    try:
      parts = shlex.split(override)
      if parts:
        return parts
    except ValueError:
      pass

  for env_name in ("VISUAL", "EDITOR"):
    raw = (env_map.get(env_name) or "").strip()
    if not raw:
      continue
    try:
      parts = shlex.split(raw)
    except ValueError:
      continue
    if parts and not _is_self_or_nested(parts):
      return parts

  vim_bin = shutil.which("vim")
  if not vim_bin:
    nix_vim = os.path.expanduser("~/.nix-profile/bin/vim")
    vim_bin = nix_vim if os.access(nix_vim, os.X_OK) else "/usr/bin/vim"
  return [vim_bin]


def get_state_dir(env: Mapping[str, str] | None = None) -> str:
  env_map = os.environ if env is None else env
  env_dir = env_map.get("CODE_HARNESS_TMUX_EDITOR_STATE_DIR")
  if env_dir:
    state_dir = env_dir
  else:
    xdg_rt = env_map.get("XDG_RUNTIME_DIR") or ""
    if xdg_rt and os.path.isdir(xdg_rt):
      state_dir = os.path.join(xdg_rt, "code-harness-tmux-editor")
    else:
      state_dir = f"/tmp/code-harness-tmux-editor-{os.getuid()}"
  os.makedirs(state_dir, mode=0o700, exist_ok=True)
  try:
    os.chmod(state_dir, 0o700)
  except OSError:
    pass
  return state_dir


def make_pane_key(socket_path: str, orig_pane: str) -> str:
  try:
    sock_real = os.path.realpath(socket_path) if socket_path else ""
  except OSError:
    sock_real = socket_path or ""
  raw = f"{sock_real}\0{orig_pane}".encode("utf-8")
  digest = hashlib.sha256(raw).hexdigest()[:12]
  slug = re.sub(r"[^A-Za-z0-9_-]", "", orig_pane) or "pane"
  return f"pane-{slug}-{digest}"


def is_prompt_file(
    target_file: str,
    config: HarnessConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
  base = os.path.basename(target_file)
  env_map = os.environ if env is None else env
  extra_globs = env_map.get("CODE_HARNESS_TMUX_EDITOR_PROMPT_GLOB") or ""
  for pat in extra_globs.split(":"):
    pat = pat.strip()
    if pat and fnmatch.fnmatch(base, pat):
      return True
  cfg = config if config is not None else load_config(env=env_map)
  for flv in ordered_flavors(cfg):
    for pat in flv.prompt_file_globs:
      if pat and fnmatch.fnmatch(base, pat):
        return True
  return False


def _unlink_if_exists(path: str) -> None:
  if not path:
    return
  try:
    os.unlink(path)
  except FileNotFoundError:
    pass


def _atomic_write_bytes(path: str, data: bytes) -> None:
  parent = os.path.dirname(os.path.abspath(path))
  fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=parent)
  try:
    with os.fdopen(fd, "wb") as f:
      f.write(data)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
  except BaseException:
    _unlink_if_exists(tmp_path)
    raise


@contextlib.contextmanager
def _pane_lock(lock_path: str):
  fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
      yield
    finally:
      fcntl.flock(fd, fcntl.LOCK_UN)
  finally:
    os.close(fd)


def list_tmux_panes(tmux_base: Sequence[str]) -> dict[str, dict] | None:
  sep = "\x1f"
  res = run_tmux(
      tmux_base,
      "list-panes",
      "-a",
      "-F",
      sep.join(["#{pane_id}", "#{pane_pid}", "#{pane_in_mode}"]),
      check=False,
  )
  if res.returncode != 0:
    return None
  panes: dict[str, dict] = {}
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 3:
      continue
    pid_s = parts[1].strip()
    try:
      pane_pid = int(pid_s)
    except ValueError:
      pane_pid = 0
    panes[parts[0]] = {
        "pid": pane_pid,
        "in_mode": parts[2].strip() == "1",
    }
  return panes


def find_active_client_tty(tmux_base: Sequence[str]) -> str:
  sep = "\x1f"
  res = run_tmux(
      tmux_base,
      "list-clients",
      "-F",
      sep.join(["#{client_tty}", "#{client_activity}"]),
      check=False,
  )
  if res.returncode != 0 or not res.stdout.strip():
    return ""
  best = (-1, "")
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 2:
      continue
    try:
      act = int(parts[1] or "0")
    except ValueError:
      act = 0
    if act > best[0]:
      best = (act, parts[0])
  return best[1]


def show_toast(
    tmux_base: Sequence[str], message: str, duration_sec: float = 1.5
) -> None:
  """Displays a brief centered popup toast."""
  client = find_active_client_tty(tmux_base)
  socket_path = (
      tmux_base[2]
      if len(tmux_base) >= 3 and tmux_base[1] == "-S"
      else None
  )
  tmux = TmuxRunner(tmux_base[0], socket_path)
  _tmux_show_toast(tmux, client, message, duration_sec=duration_sec)


def _split_env_args(config: HarnessConfig | None = None) -> list[str]:
  names: list[str] = list(FORWARD_ENV_VARS)
  if config is not None:
    for extra in (config.env_bin, config.env_data_dir):
      if extra and extra not in names:
        names.append(extra)
    for flv in config.flavors.values():
      if flv.env_bin and flv.env_bin not in names:
        names.append(flv.env_bin)

  args: list[str] = []
  for name in names:
    val = os.environ.get(name)
    if val is not None:
      args.extend(["-e", f"{name}={val}"])
  return args


def _spawn_split_pane(
    tmux_base: Sequence[str],
    orig_pane: str,
    size: str,
    worker_cmd: Sequence[str],
    config: HarnessConfig | None = None,
) -> tuple[int, str, str]:
  cmd = [*tmux_base, "split-window", "-h"]
  if size:
    cmd.extend(["-l", size])
  cmd.extend([
      "-P",
      "-F",
      "#{pane_id}",
      "-t",
      orig_pane,
      "-c",
      "#{pane_current_path}",
      *_split_env_args(config),
      f"exec {shlex.join(worker_cmd)}",
  ])
  res = subprocess.run(
      cmd,
      stdin=subprocess.DEVNULL,
      capture_output=True,
      text=True,
      check=False,
  )
  return res.returncode, res.stdout.strip(), res.stderr.strip()


def _install_signal_handlers(child_holder: dict) -> None:
  def _handle_sig(signum, _frame):
    proc = child_holder.get("proc")
    if proc is not None and proc.poll() is None:
      try:
        proc.terminate()
      except OSError:
        pass
    raise _SignalExit(signum)

  signal.signal(signal.SIGHUP, _handle_sig)
  signal.signal(signal.SIGTERM, _handle_sig)


def _run_vim_foreground(
    vim_cmd: Sequence[str], vim_extra_args: Sequence[str], path: str
) -> tuple[int, bool]:
  child_holder: dict = {}
  _install_signal_handlers(child_holder)
  try:
    proc = subprocess.Popen([*vim_cmd, *vim_extra_args, path])
  except OSError as exc:
    print(
        f"code-harness tmux-editor: failed to launch editor: {exc}",
        file=sys.stderr,
    )
    return 127, False
  child_holder["proc"] = proc
  while True:
    try:
      return proc.wait(), False
    except KeyboardInterrupt:
      # Let Vim handle Ctrl-C rather than aborting the wrapper early.
      continue
    except _SignalExit:
      try:
        proc.wait(timeout=1.0)
      except subprocess.TimeoutExpired:
        try:
          proc.kill()
          proc.wait(timeout=1.0)
        except OSError:
          pass
      return 1, True


def _norm_trailing_nl(data: bytes) -> bytes:
  return data.rstrip(b"\n")


def run_blocking_split(
    tmux_base: Sequence[str],
    socket_path: str,
    orig_pane: str,
    size: str,
    vim_args: Sequence[str],
    target_file: str,
    state_dir: str,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
) -> int:
  abs_target = os.path.abspath(target_file)
  fd, status_path = tempfile.mkstemp(
      prefix="code-harness-block-", suffix=".rc", dir=state_dir
  )
  os.close(fd)
  wait_channel = f"code-harness-edit-{os.getpid()}-{uuid.uuid4().hex[:12]}"
  worker_cmd = [
      *_self_editor_cmd(flavor=flavor),
      "--blocking-worker",
      "--channel",
      wait_channel,
      "--status-file",
      status_path,
      "--target",
      abs_target,
  ]
  if socket_path:
    worker_cmd.extend(["--socket", socket_path])
  if vim_args:
    worker_cmd.append("--")
    worker_cmd.extend(vim_args)

  try:
    rc, editor_pane, err = _spawn_split_pane(
        tmux_base, orig_pane, size, worker_cmd, config=config
    )
    if rc != 0 or not editor_pane:
      if err:
        print(
            f"code-harness tmux-editor: split failed: {err}", file=sys.stderr
        )
      return 1

    wait_proc = subprocess.Popen(
        [*tmux_base, "wait-for", wait_channel],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    while True:
      try:
        wait_proc.wait(timeout=0.2)
        break
      except subprocess.TimeoutExpired:
        panes = list_tmux_panes(tmux_base)
        if panes is None or editor_pane not in panes:
          try:
            wait_proc.wait(timeout=0.15)
          except subprocess.TimeoutExpired:
            wait_proc.terminate()
            wait_proc.wait()
          break

    try:
      with open(status_path, encoding="utf-8") as f:
        raw_rc = f.read().strip()
      return int(raw_rc) if raw_rc else 1
    except (OSError, ValueError):
      return 1
  finally:
    _unlink_if_exists(status_path)


def run_blocking_worker(argv: Sequence[str]) -> int:
  if "--" in argv:
    idx = argv.index("--")
    opts_argv = argv[:idx]
    vim_extra_args = argv[idx + 1 :]
  else:
    opts_argv = argv
    vim_extra_args = []

  opts = _parse_flag_pairs(
      opts_argv, {"--channel", "--status-file", "--target", "--socket"}
  )
  channel = opts["--channel"]
  status_path = opts["--status-file"]
  target_file = opts["--target"]
  socket_path = opts.get("--socket", "")

  tmux_base = tmux_base_cmd(socket_path)
  vim_cmd = resolve_vim_command()
  rc = 1
  try:
    rc, signaled = _run_vim_foreground(vim_cmd, vim_extra_args, target_file)
    if signaled:
      rc = 1
  finally:
    try:
      _atomic_write_bytes(status_path, f"{rc}\n".encode("utf-8"))
    except OSError:
      pass
    run_tmux(tmux_base, "wait-for", "-S", channel, check=False)
  return 0


def run_prompt_two_stage(
    tmux_base: Sequence[str],
    socket_path: str,
    orig_pane: str,
    size: str,
    vim_args: Sequence[str],
    target_file: str,
    state_dir: str,
    config: HarnessConfig | None = None,
    flavor: str | None = None,
) -> int:
  pane_key = make_pane_key(socket_path, orig_pane)
  ready_path = os.path.join(state_dir, f"{pane_key}.ready")
  session_path = os.path.join(state_dir, f"{pane_key}.json")
  lock_path = os.path.join(state_dir, f"{pane_key}.lock")

  with _pane_lock(lock_path):
    # Pass 2: apply staged prompt from closed editor split.
    if os.path.isfile(ready_path):
      with open(ready_path, "rb") as f:
        ready_bytes = f.read()
      _unlink_if_exists(ready_path)
      _unlink_if_exists(session_path)
      with open(target_file, "wb") as f:
        f.write(ready_bytes)
      return 0

    # Existing editor split still alive: focus it instead of opening another.
    if os.path.isfile(session_path):
      editor_pane = ""
      stale_draft = ""
      try:
        with open(session_path, encoding="utf-8") as f:
          sess = json.load(f)
        if isinstance(sess, dict):
          editor_pane = sess.get("editor_pane") or ""
          stale_draft = sess.get("draft_path") or ""
      except (OSError, ValueError):
        pass

      if editor_pane:
        panes = list_tmux_panes(tmux_base)
        if panes is not None and editor_pane in panes:
          run_tmux(tmux_base, "select-window", "-t", editor_pane, check=False)
          run_tmux(tmux_base, "select-pane", "-t", editor_pane, check=False)
          return 0

      _unlink_if_exists(session_path)
      _unlink_if_exists(stale_draft)

    # Pass 1: seed unique draft file and open non-blocking split.
    initial_bytes = b""
    if os.path.isfile(target_file):
      with open(target_file, "rb") as f:
        initial_bytes = f.read()

    fd, draft_path = tempfile.mkstemp(
        prefix="code-harness-edit-", suffix=".txt", dir=state_dir
    )
    with os.fdopen(fd, "wb") as f:
      f.write(initial_bytes)
    now = time.time()
    os.utime(draft_path, (now - 2.0, now - 2.0))

    init_fd, init_sentinel = tempfile.mkstemp(prefix=".init-", dir=state_dir)
    os.close(init_fd)

  worker_cmd = [
      *_self_editor_cmd(flavor=flavor),
      "--split-worker",
      "--orig-pane",
      orig_pane,
      "--draft",
      draft_path,
      "--session-file",
      session_path,
      "--ready-file",
      ready_path,
      "--lock-file",
      lock_path,
      "--init-sentinel",
      init_sentinel,
  ]
  if socket_path:
    worker_cmd.extend(["--socket", socket_path])
  if vim_args:
    worker_cmd.append("--")
    worker_cmd.extend(vim_args)

  rc, editor_pane, err = _spawn_split_pane(
      tmux_base, orig_pane, size, worker_cmd, config=config
  )
  if rc != 0 or not editor_pane:
    _unlink_if_exists(init_sentinel)
    _unlink_if_exists(draft_path)
    if err:
      print(f"code-harness tmux-editor: split failed: {err}", file=sys.stderr)
    return 1

  with _pane_lock(lock_path):
    # Only record session if the worker has not already exited and cleaned up.
    if os.path.exists(init_sentinel):
      _unlink_if_exists(init_sentinel)
      sess_bytes = json.dumps({
          "editor_pane": editor_pane,
          "draft_path": draft_path,
      }).encode("utf-8")
      _atomic_write_bytes(session_path, sess_bytes)

  return 0


def run_split_worker(
    argv: Sequence[str], flavor: str | None = None
) -> int:
  if "--" in argv:
    idx = argv.index("--")
    opts_argv = argv[:idx]
    vim_extra_args = argv[idx + 1 :]
  else:
    opts_argv = argv
    vim_extra_args = []

  opts = _parse_flag_pairs(
      opts_argv,
      {
          "--orig-pane",
          "--draft",
          "--session-file",
          "--ready-file",
          "--lock-file",
          "--init-sentinel",
          "--socket",
      },
  )
  orig_pane = opts["--orig-pane"]
  draft_path = opts["--draft"]
  session_path = opts["--session-file"]
  ready_path = opts["--ready-file"]
  lock_path = opts["--lock-file"]
  init_sentinel = opts["--init-sentinel"]
  socket_path = opts.get("--socket", "")
  editor_pane = os.environ.get("TMUX_PANE") or ""

  vim_cmd = resolve_vim_command()
  st0 = os.stat(draft_path)
  initial_mtime_ns = st0.st_mtime_ns
  with open(draft_path, "rb") as f:
    initial_bytes = f.read()

  vim_rc = 1
  signaled = False
  final_bytes = b""
  final_mtime_ns = None
  try:
    vim_rc, signaled = _run_vim_foreground(vim_cmd, vim_extra_args, draft_path)
    if os.path.isfile(draft_path):
      final_mtime_ns = os.stat(draft_path).st_mtime_ns
      with open(draft_path, "rb") as f:
        final_bytes = f.read()
  finally:
    with _pane_lock(lock_path):
      _unlink_if_exists(init_sentinel)
      _unlink_if_exists(draft_path)
      _unlink_if_exists(session_path)

  if signaled or vim_rc != 0:
    return 0

  was_written = (
      final_mtime_ns is not None and final_mtime_ns != initial_mtime_ns
  )
  should_apply = (
      _norm_trailing_nl(final_bytes) != _norm_trailing_nl(initial_bytes)
      or (was_written and _norm_trailing_nl(initial_bytes) != b"")
  )
  if not should_apply:
    return 0

  _atomic_write_bytes(ready_path, final_bytes)
  apply_cmd = [
      *_self_editor_cmd(flavor=flavor),
      "--apply-worker",
      "--orig-pane",
      orig_pane,
      "--editor-pane",
      editor_pane,
      "--ready-file",
      ready_path,
  ]
  if socket_path:
    apply_cmd.extend(["--socket", socket_path])

  subprocess.Popen(
      apply_cmd,
      stdin=subprocess.DEVNULL,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
  return 0


def _read_ready_bytes(ready_path: str) -> bytes:
  try:
    with open(ready_path, "rb") as f:
      return f.read()
  except OSError:
    return b""


def _stage_into_buffer(
    tmux_base: Sequence[str],
    ready_path: str,
    message: str,
    drop_ready: bool = False,
) -> None:
  data = _read_ready_bytes(ready_path)
  if drop_ready:
    _unlink_if_exists(ready_path)
  load_tmux_buffer(tmux_base, data)
  show_toast(tmux_base, message)


def run_apply_worker(
    argv: Sequence[str], config: HarnessConfig | None = None
) -> int:
  opts = _parse_flag_pairs(
      argv, {"--orig-pane", "--editor-pane", "--ready-file", "--socket"}
  )
  orig_pane = opts["--orig-pane"]
  editor_pane = opts.get("--editor-pane", "")
  ready_path = opts["--ready-file"]
  socket_path = opts.get("--socket", "")
  tmux_base = tmux_base_cmd(socket_path)

  # Wait until the split pane finishes closing so orig_pane regains full width.
  panes = None
  deadline = time.monotonic() + 1.0
  while True:
    panes = list_tmux_panes(tmux_base)
    if panes is None:
      return 0
    if not editor_pane or editor_pane not in panes:
      break
    if time.monotonic() >= deadline:
      break
    time.sleep(0.015)

  if not os.path.exists(ready_path):
    return 0

  if orig_pane not in panes:
    _stage_into_buffer(
        tmux_base,
        ready_path,
        "Original pane closed; saved prompt to tmux buffer",
        drop_ready=True,
    )
    return 0

  force_send = (
      os.environ.get("CODE_HARNESS_TMUX_EDITOR_FORCE_SEND") == "1"
      or os.environ.get("CODE_HARNESS_TMUX_EDITOR_SKIP_PROC_CHECK") == "1"
  )
  if not force_send:
    cfg = config if config is not None else load_config()
    orig_pid = panes[orig_pane]["pid"]
    procs, children_map = load_process_table(
        config=cfg, root_pids={orig_pid}
    )
    cli_proc, cli_is_print = find_pane_cli(
        orig_pid, procs, children_map, config=cfg
    )
    if cli_proc is None:
      _stage_into_buffer(
          tmux_base,
          ready_path,
          "Agent exited; saved prompt to tmux buffer",
          drop_ready=True,
      )
      return 0
    state = None
    try:
      data_dir = default_data_dir(config=cfg)
      mcp_sigs = load_mcp_commands(data_dir, config=cfg)
      self_ignored = self_ignored_pids(procs, children_map)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, orig_pane, data_dir=data_dir, config=cfg
      )
      state, _ = classify_cli_state(
          cli_proc,
          cli_is_print,
          orig_pane,
          conv_id,
          open_dbs,
          procs,
          children_map,
          mcp_sigs,
          self_ignored,
          cli_dir,
          config=cfg,
      )
    except (OSError, sqlite3.Error, ValueError):
      if os.path.isdir("/proc") and not os.path.exists(
          f"/proc/{cli_proc['pid']}"
      ):
        _stage_into_buffer(
            tmux_base,
            ready_path,
            "Agent exited; saved prompt to tmux buffer",
            drop_ready=True,
        )
        return 0

    if state is not None and is_urgent_cli_state(state):
      _stage_into_buffer(
          tmux_base,
          ready_path,
          "Prompt staged: dismiss modal then press C-g",
          drop_ready=False,
      )
      return 0

  if panes[orig_pane]["in_mode"]:
    run_tmux(tmux_base, "copy-mode", "-q", "-t", orig_pane, check=False)

  run_tmux(tmux_base, "send-keys", "-t", orig_pane, "C-g", check=False)

  consume_deadline = time.monotonic() + 0.6
  while time.monotonic() < consume_deadline:
    if not os.path.exists(ready_path):
      return 0
    time.sleep(0.02)

  if os.path.exists(ready_path):
    _stage_into_buffer(
        tmux_base,
        ready_path,
        "Prompt staged: close panel then press C-g",
        drop_ready=False,
    )
  return 0


def _parse_flag_pairs(
    argv: Sequence[str], allowed_flags: set[str]
) -> dict[str, str]:
  out: dict[str, str] = {}
  i = 0
  while i < len(argv):
    tok = argv[i]
    if tok not in allowed_flags or i + 1 >= len(argv):
      raise ValueError(f"unexpected argument: {tok}")
    out[tok] = argv[i + 1]
    i += 2
  return out


def _parse_external_args(argv: Sequence[str]) -> tuple[str, list[str], str]:
  size = os.environ.get("CODE_HARNESS_TMUX_EDITOR_SPLIT_SIZE") or ""
  rest: list[str] = []
  i = 0
  while i < len(argv):
    tok = argv[i]
    if tok == "--":
      rest.extend(argv[i:])
      break
    if tok == "--size":
      if i + 1 >= len(argv):
        raise ValueError("--size requires an argument")
      size = argv[i + 1]
      i += 2
      continue
    if tok.startswith("--size="):
      size = tok.split("=", 1)[1]
      i += 1
      continue
    rest.append(tok)
    i += 1
  if not rest:
    raise ValueError("missing target file")
  return size, rest[:-1], rest[-1]


def cmd_tmux_editor(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  if argv:
    mode = argv[0]
    if mode == "--split-worker":
      return run_split_worker(argv[1:], flavor=flavor)
    if mode == "--apply-worker":
      return run_apply_worker(argv[1:], config=config)
    if mode == "--blocking-worker":
      return run_blocking_worker(argv[1:])
    if len(argv) == 1 and mode in ("-h", "--help"):
      print(
          "Usage: code-harness tmux-editor [--size SIZE] [vim_args...] <file>"
      )
      return 0

  try:
    size, vim_args, target_file = _parse_external_args(argv)
  except ValueError as exc:
    print(f"code-harness tmux-editor: {exc}", file=sys.stderr)
    return 2

  vim_cmd = resolve_vim_command()
  tmux_env = os.environ.get("TMUX") or ""
  orig_pane = os.environ.get("TMUX_PANE") or ""
  if not tmux_env or not orig_pane:
    os.execvp(vim_cmd[0], [*vim_cmd, *vim_args, target_file])
    return 0

  socket_path = tmux_env.split(",", 1)[0]
  tmux_base = tmux_base_cmd(socket_path)
  state_dir = get_state_dir()

  if is_prompt_file(target_file, config=config):
    return run_prompt_two_stage(
        tmux_base,
        socket_path,
        orig_pane,
        size,
        vim_args,
        target_file,
        state_dir,
        config=config,
        flavor=flavor,
    )
  return run_blocking_split(
      tmux_base,
      socket_path,
      orig_pane,
      size,
      vim_args,
      target_file,
      state_dir,
      config=config,
      flavor=flavor,
  )

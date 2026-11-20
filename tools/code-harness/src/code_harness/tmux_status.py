"""Render coding-agent CLI attention status for the tmux right status line.

Inspects all tmux panes (using code_harness.snapshot detection logic) and
prints a compact segment reflecting the 5-tier priority model
(`perm` > `ask` > `plan` > `done` > `work`):
  1. Session-name segment (fit to --max-width):
     - Urgent session name(s): `! <perm>` in coral bold and `? <ask>` in gold
       bold.
     - Otherwise, if any CLI is waiting on plan approval (`plan`), the most
       recent plan session with `◆ <session>` in magenta bold (without
       requiring a bell).
     - Otherwise, the most recently settled belled `done` session in green.
     (Ties break via ctrl-space F next-window order.)
  2. Always-shown counts: `◆ <plan> ● <done> ◐ <work>`.

While scanning all panes, it also reconciles per-pane `@code_harness_context`
badges and global `@code_harness_context_pad` for `status-left` (via
`code_harness.context`), cleans up legacy session/window context options when
present, and reconciles per-pane `@code_harness_title` options for dynamic tmux
window names.

Urgent prompts ("perm" and "ask", top-level or subagent) remain shown without
requiring a bell and emit an OSC 777 toast via `code_harness.tmux_notify` on
newly observed prompts when no attached tmux client is focused. Plan-approval
sessions (`plan`) surface their session name and `◆` counter unconditionally
without firing urgent OSC 777 toasts. Settled "done" sessions require
`window_bell_flag` to surface their session name.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

from code_harness import context, tmux_notify
from code_harness.config import HarnessConfig
from code_harness.snapshot import (
    TmuxRunner,
    _transcript_path,
    classify_cli_state_with_ts,
    classify_ui_category,
    cli_proc_flavor,
    compact_window_title,
    conversation_labels,
    default_data_dir,
    find_pane_cli,
    find_tmux_bin,
    harness_data_dirs,
    is_generic_review_title,
    is_subagent_db,
    load_mcp_commands,
    load_process_table,
    resolve_cli_conversation_and_dir,
    self_ignored_pids,
    urgent_waiting_signature,
)

ISO_FRAC_RE = re.compile(r"\.(\d+)(?=[+-]|$)")
NOTIFY_SEEN_GRACE_SEC = 15.0


def list_tmux_windows_and_panes(tmux: TmuxRunner) -> list[dict] | None:
  """Returns list of windows in `list-panes -a` order, or None if no server."""
  sep = "\x1f"
  res = tmux.run(
      "list-panes",
      "-a",
      "-F",
      sep.join([
          "#{session_name}",
          "#{window_index}",
          "#{window_bell_flag}",
          "#{pane_index}",
          "#{pane_id}",
          "#{pane_pid}",
          "#{@code_harness_context}",
          "#{@code_harness_context_pad}",
          "#{status-left}",
          "#{@code_harness_title}",
          "#{automatic-rename}",
          "#{window_name}",
      ]),
      check=False,
  )
  if res.returncode != 0:
    return None
  if not res.stdout.strip():
    return []

  windows: list[dict] = []
  by_key: dict[tuple[str, str], dict] = {}
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) == 6:
      sname, widx, wbell, pidx_s, pane_id, ppid_s = parts
      ctx_badge, ctx_pad, status_left = "", "", ""
      ch_title, auto_rename, window_name = "", "", ""
      has_ctx_fields = False
      has_title_fields = False
    elif len(parts) == 9:
      (
          sname,
          widx,
          wbell,
          pidx_s,
          pane_id,
          ppid_s,
          ctx_badge,
          ctx_pad,
          status_left,
      ) = parts
      ch_title, auto_rename, window_name = "", "", ""
      has_ctx_fields = True
      has_title_fields = False
    elif len(parts) == 12:
      (
          sname,
          widx,
          wbell,
          pidx_s,
          pane_id,
          ppid_s,
          ctx_badge,
          ctx_pad,
          status_left,
          ch_title,
          auto_rename,
          window_name,
      ) = parts
      has_ctx_fields = True
      has_title_fields = True
    else:
      continue
    try:
      pidx = int(pidx_s)
      ppid = int(ppid_s)
    except ValueError:
      continue
    key = (sname, widx)
    win = by_key.get(key)
    if win is None:
      win = {
          "session_name": sname,
          "window_index": widx,
          "bell": wbell == "1",
          "status_left": status_left,
          "has_ctx_fields": has_ctx_fields,
          "has_title_fields": has_title_fields,
          "panes": [],
      }
      by_key[key] = win
      windows.append(win)
    else:
      if wbell == "1":
        win["bell"] = True
      if has_ctx_fields:
        win["has_ctx_fields"] = True
      if has_title_fields:
        win["has_title_fields"] = True
    win["panes"].append({
        "index": pidx,
        "pane_id": pane_id,
        "pane_pid": ppid,
        "context_badge": ctx_badge,
        "context_pad": ctx_pad,
        "status_left": status_left,
        "has_ctx_fields": has_ctx_fields,
        "code_harness_title": ch_title,
        "automatic_rename": auto_rename,
        "window_name": window_name,
        "has_title_fields": has_title_fields,
    })

  for win in windows:
    win["panes"].sort(key=lambda p: p["index"])
  return windows


def current_window_location(
    tmux: TmuxRunner,
    tmux_socket: str | None = None,
    clients: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
  """Returns (session_name, window_index) for active client, or (None, None)."""
  sep = "\x1f"
  best: tuple[int, str, str] = (-1, "", "")
  if clients is not None:
    for c in clients:
      ctty = c.get("tty") or ""
      if not ctty:
        continue
      act = c.get("activity") or 0
      if act > best[0]:
        best = (act, c.get("session") or "", ctty)
  else:
    c_res = tmux.run(
        "list-clients",
        "-F",
        sep.join(["#{client_session}", "#{client_activity}", "#{client_tty}"]),
        check=False,
    )
    if c_res.returncode == 0 and c_res.stdout.strip():
      for line in c_res.stdout.splitlines():
        parts = line.split(sep)
        if len(parts) != 3:
          continue
        csess, act_s, ctty = parts
        try:
          act = int(act_s or "0")
        except ValueError:
          act = 0
        if act > best[0]:
          best = (act, csess, ctty)

  if best[2]:
    d_res = tmux.run(
        "display-message",
        "-t",
        best[2],
        "-p",
        sep.join(["#{session_name}", "#{window_index}"]),
        check=False,
    )
    if d_res.returncode == 0 and d_res.stdout.strip():
      parts = d_res.stdout.rstrip("\r\n").split(sep)
      if len(parts) == 2:
        return parts[0], parts[1]

  my_pane = os.environ.get("TMUX_PANE") or ""
  if my_pane:
    tmux_env = os.environ.get("TMUX") or ""
    sock_matches = (
        not tmux_socket
        or tmux_env.split(",", 1)[0] == os.path.abspath(tmux_socket)
    )
    if sock_matches:
      d_res = tmux.run(
          "display-message",
          "-t",
          my_pane,
          "-p",
          sep.join(["#{session_name}", "#{window_index}"]),
          check=False,
      )
      if d_res.returncode == 0 and d_res.stdout.strip():
        parts = d_res.stdout.rstrip("\r\n").split(sep)
        if len(parts) == 2:
          return parts[0], parts[1]

  return None, None


def parse_iso_epoch(iso_str: str) -> float | None:
  """Parses ISO-8601 timestamp string to UTC epoch float, or None."""
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
    return dt.timestamp()
  except (ValueError, OSError):
    return None


def conv_file_mtime(
    data_dir: str | None,
    conv_id: str | None,
    config: HarnessConfig | None = None,
) -> float | None:
  """Returns transcript.jsonl mtime, falling back to .db-wal / .db mtime."""
  if not conv_id:
    return None
  candidates = [_transcript_path(data_dir, conv_id, config=config)]
  for d in harness_data_dirs(data_dir, config=config):
    candidates.append(os.path.join(d, "conversations", f"{conv_id}.db-wal"))
    candidates.append(os.path.join(d, "conversations", f"{conv_id}.db"))
  for path in candidates:
    try:
      return os.path.getmtime(path)
    except OSError:
      continue
  return None


def candidate_recency(
    data_dir: str | None,
    conv_id: str | None,
    last_ts: str,
    config: HarnessConfig | None = None,
) -> tuple[int, float, float]:
  """Returns comparable recency tuple for a plan/done candidate."""
  iso_epoch = parse_iso_epoch(last_ts)
  file_mtime = conv_file_mtime(data_dir, conv_id, config=config)
  primary_ts = iso_epoch if iso_epoch is not None else file_mtime
  if primary_ts is not None:
    return (1, primary_ts, file_mtime or 0.0)
  return (0, 0.0, 0.0)


def truncate_left_with_prefix(prefix: str, body: str, max_width: int) -> str:
  """Fits `prefix + body` within `max_width`, left-truncating with `…`."""
  if max_width <= 0:
    return ""
  raw = prefix + body
  if len(raw) <= max_width:
    return raw
  if prefix:
    if max_width <= len(prefix):
      return prefix[:max_width]
    if max_width == len(prefix) + 1:
      return prefix + "…"
    keep = max_width - len(prefix) - 1
    return f"{prefix}…{body[-keep:]}"
  if max_width == 1:
    return "…"
  return f"…{body[-(max_width - 1):]}"


def pick_done_winner(
    candidates: list[dict],
    windows: list[dict],
    tmux: TmuxRunner,
    tmux_socket: str | None = None,
    clients: Sequence[Mapping[str, Any]] | None = None,
    get_clients: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> dict:
  """Picks the most recent candidate, breaking ties via F ordering."""
  max_recency = max(c["recency"] for c in candidates)
  tied = [c for c in candidates if c["recency"] == max_recency]
  if len(tied) == 1:
    return tied[0]

  cur_idx = -1
  if len({c["session_name"] for c in tied}) > 1 or len(
      {c["win_idx"] for c in tied}
  ) > 1:
    cur_clients = (
        clients
        if clients is not None
        else (get_clients() if get_clients is not None else None)
    )
    cur_sess, cur_win = current_window_location(
        tmux, tmux_socket=tmux_socket, clients=cur_clients
    )
    if cur_sess is not None and cur_win is not None:
      for idx, win in enumerate(windows):
        if win["session_name"] == cur_sess and win["window_index"] == cur_win:
          cur_idx = idx
          break

  total = len(windows)

  def rank_key(cand: dict) -> tuple:
    widx = cand["win_idx"]
    step = (widx - cur_idx - 1) % total if cur_idx >= 0 else widx
    return (cand["recency"], -step, -cand["pane_pos"])

  return max(tied, key=rank_key)


def format_urgent_segments(
    perm_sessions: list[str],
    ask_sessions: list[str],
    max_width: int,
    plain: bool,
) -> str:
  """Formats urgent `! <perm>` and `? <ask>` segments within `max_width`."""
  if (not perm_sessions and not ask_sessions) or max_width <= 0:
    return ""

  def esc(text: str) -> str:
    return text if plain else text.replace("#", "##")

  def style_perm(vis: str) -> str:
    if not vis:
      return ""
    if plain:
      return f"{vis} "
    return f"#[fg=colour203,bold]{esc(vis)}#[nobold] "

  def style_ask(vis: str) -> str:
    if not vis:
      return ""
    if plain:
      return f"{vis} "
    return f"#[fg=colour220,bold]{esc(vis)}#[nobold] "

  if perm_sessions and not ask_sessions:
    return style_perm(
        truncate_left_with_prefix("! ", " ".join(perm_sessions), max_width)
    )
  if ask_sessions and not perm_sessions:
    return style_ask(
        truncate_left_with_prefix("? ", " ".join(ask_sessions), max_width)
    )

  perm_body = " ".join(perm_sessions)
  ask_body = " ".join(ask_sessions)
  raw_perm = f"! {perm_body}"
  raw_ask = f"? {ask_body}"
  if len(raw_perm) + 1 + len(raw_ask) <= max_width:
    vis_perm, vis_ask = raw_perm, raw_ask
  elif max_width < 8:
    vis_perm = truncate_left_with_prefix("! ", perm_body, max_width)
    vis_ask = ""
  else:
    ask_budget = min(len(raw_ask), max(4, max_width // 3))
    perm_budget = max_width - ask_budget - 1
    if len(raw_perm) < perm_budget:
      ask_budget = min(
          len(raw_ask), ask_budget + (perm_budget - len(raw_perm))
      )
      perm_budget = len(raw_perm)
    vis_perm = truncate_left_with_prefix("! ", perm_body, perm_budget)
    vis_ask = truncate_left_with_prefix("? ", ask_body, ask_budget)

  return style_perm(vis_perm) + style_ask(vis_ask)


def format_status_line(
    perm_sessions: list[str],
    ask_sessions: list[str],
    plan_candidates: list[dict],
    plan_count: int,
    done_candidates: list[dict],
    done_count: int,
    work_count: int,
    windows: list[dict],
    tmux: TmuxRunner,
    tmux_socket: str | None,
    max_width: int,
    plain: bool,
    clients: Sequence[Mapping[str, Any]] | None = None,
    get_clients: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> str:
  """Formats the tmux status-right coding-agent indicator string."""
  parts: list[str] = []

  def esc(text: str) -> str:
    return text if plain else text.replace("#", "##")

  urgent = format_urgent_segments(perm_sessions, ask_sessions, max_width, plain)
  if urgent:
    parts.append(urgent)
  elif plan_candidates:
    winner = pick_done_winner(
        plan_candidates,
        windows,
        tmux,
        tmux_socket=tmux_socket,
        clients=clients,
        get_clients=get_clients,
    )
    vis = truncate_left_with_prefix("◆ ", winner["session_name"], max_width)
    if vis:
      if plain:
        parts.append(f"{vis} ")
      else:
        parts.append(f"#[fg=colour206,bold]{esc(vis)}#[nobold] ")
  elif done_candidates:
    winner = pick_done_winner(
        done_candidates,
        windows,
        tmux,
        tmux_socket=tmux_socket,
        clients=clients,
        get_clients=get_clients,
    )
    vis = truncate_left_with_prefix("", winner["session_name"], max_width)
    if vis:
      if plain:
        parts.append(f"{vis} ")
      else:
        parts.append(f"#[fg=colour78]{esc(vis)} ")

  if plain:
    parts.append(f"◆ {plan_count} ")
    parts.append(f"● {done_count} ")
    parts.append(f"◐ {work_count} ")
  else:
    if plan_count > 0:
      parts.append(f"#[fg=colour206,bold]◆ {plan_count}#[nobold] ")
    else:
      parts.append("#[fg=colour206]◆ 0 ")
    if done_count > 0:
      parts.append(f"#[fg=colour78,bold]● {done_count}#[nobold] ")
    else:
      parts.append("#[fg=colour78]● 0 ")
    if work_count > 0:
      parts.append(f"#[fg=colour75,bold]◐ {work_count}#[nobold] #[default]")
    else:
      parts.append("#[fg=colour75]◐ 0 #[default]")

  return "".join(parts)


def notify_state_path(tmux_socket: str | None = None) -> str:
  """Returns per-server state JSON path for OSC 777 prompt deduplication."""
  override = os.environ.get("CODE_HARNESS_TMUX_NOTIFY_STATE")
  if override:
    return override
  xdg_rt = os.environ.get("XDG_RUNTIME_DIR") or ""
  if xdg_rt:
    base_dir = xdg_rt
  else:
    xdg_st = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(
        "~/.local/state"
    )
    base_dir = os.path.join(xdg_st, "code-harness-tmux")
  eff_socket = tmux_socket
  if not eff_socket:
    tmux_env = os.environ.get("TMUX") or ""
    if tmux_env:
      eff_socket = tmux_env.split(",", 1)[0]
  if eff_socket:
    try:
      norm_sock = os.path.realpath(eff_socket)
    except OSError:
      norm_sock = os.path.abspath(eff_socket)
    digest = hashlib.sha256(norm_sock.encode("utf-8")).hexdigest()[:12]
    fname = f"code-harness-tmux-notify-{digest}.json"
  else:
    fname = "code-harness-tmux-notify.json"
  return os.path.join(base_dir, fname)


def _load_seen_state(
    state_path: str, now: float | None = None
) -> tuple[dict[tuple[str, ...], float], bool]:
  if now is None:
    now = time.time()
  try:
    with open(state_path, encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, ValueError):
    return {}, False
  if not isinstance(data, dict):
    return {}, False

  out: dict[tuple[str, ...], float] = {}
  pruned = False
  raw_entries = data.get("seen_entries")
  if isinstance(raw_entries, list):
    for item in raw_entries:
      if not isinstance(item, dict):
        pruned = True
        continue
      k = item.get("key")
      ts = item.get("ts")
      if (
          not isinstance(k, list)
          or len(k) != 4
          or not all(isinstance(x, str) for x in k)
          or isinstance(ts, bool)
          or not isinstance(ts, (int, float))
      ):
        pruned = True
        continue
      ts_f = float(ts)
      if now - ts_f > NOTIFY_SEEN_GRACE_SEC:
        pruned = True
        continue
      out[tuple(k)] = ts_f
    return out, pruned

  raw_seen = data.get("seen")
  if not isinstance(raw_seen, list):
    return {}, False
  for item in raw_seen:
    if (
        isinstance(item, list)
        and len(item) == 4
        and all(isinstance(x, str) for x in item)
    ):
      out[tuple(item)] = float(now)
  return out, False


def _load_seen_keys(
    state_path: str, now: float | None = None
) -> dict[tuple[str, ...], float]:
  seen_map, _ = _load_seen_state(state_path, now=now)
  return seen_map


def _save_seen_keys(
    state_path: str,
    seen_map: Mapping[tuple[str, ...], float] | Sequence[tuple[str, ...]],
) -> None:
  if isinstance(seen_map, Mapping):
    items = sorted(seen_map.items(), key=lambda kv: kv[1], reverse=True)[:256]
  else:
    now = time.time()
    items = [(tuple(k), now) for k in list(seen_map)[:256]]
  parent = os.path.dirname(os.path.abspath(state_path))
  tmp_path = None
  try:
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=parent)
    try:
      os.fchmod(fd, 0o600)
    except OSError:
      pass
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(
          {
              "seen": [list(k) for k, _ in items],
              "seen_entries": [{"key": list(k), "ts": ts} for k, ts in items],
          },
          f,
      )
    os.replace(tmp_path, state_path)
  except OSError:
    if tmp_path:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass


def format_urgent_toast(new_items: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
  """Returns (title, body) for newly observed urgent agent prompts."""
  if len(new_items) == 1:
    it = new_items[0]
    kind = "permission" if it["category"] == "perm" else "question"
    title = f"Agent {kind}: {it['session_name']}"
    reason = it.get("reason") or f"waiting on {kind}"
    body = f"{reason} (C-Space f)"
    return title, body
  sessions = ", ".join(dict.fromkeys(str(i["session_name"]) for i in new_items))
  title = f"Agent attention: {sessions}"
  body = f"{len(new_items)} prompts waiting (C-Space f)"
  return title, body


def urgent_prompt_token(
    cli_dir: str | None,
    conv_id: str | None,
    cat: str,
    reason: str,
    last_ts: str,
    open_dbs: set[str] | tuple[str, ...] = (),
    cli_start_epoch: float | None = None,
    config: HarnessConfig | None = None,
) -> str:
  """Returns stable dedup token for an urgent prompt."""
  del cat
  if cli_dir and conv_id and os.path.isdir(cli_dir):
    sig = urgent_waiting_signature(
        cli_dir,
        conv_id,
        open_dbs=open_dbs,
        cli_start_epoch=cli_start_epoch,
        config=config,
    )
    if sig:
      return sig
  return last_ts or reason


def maybe_send_urgent_notifications(
    tmux: TmuxRunner,
    urgent_items: Sequence[Mapping[str, Any]],
    tmux_socket: str | None,
    notify_enabled: bool,
    get_clients: Callable[[], list[dict[str, Any]] | None],
    now: float | None = None,
    notify_mod: Any = tmux_notify,
) -> None:
  """Emits OSC 777 notifications once per urgent prompt when unfocused."""
  if not notify_enabled or notify_mod is None:
    return
  if now is None:
    now = time.time()
  state_path = notify_state_path(tmux_socket)
  seen_map, pruned = _load_seen_state(state_path, now=now)
  current_keys = [it["key"] for it in urgent_items]
  need_save = pruned
  for k in current_keys:
    if k in seen_map:
      if now - seen_map[k] > 5.0:
        need_save = True
      seen_map[k] = now

  new_items = [it for it in urgent_items if it["key"] not in seen_map]
  mark_new_seen = False
  if new_items:
    clients = get_clients()
    attached = [c for c in (clients or []) if c.get("tty")]
    if attached:
      title, body = format_urgent_toast(new_items)
      sent = notify_mod.send_tmux_notification(
          tmux, title, body, if_unfocused=True, clients=clients
      )
      if sent > 0 or notify_mod.any_client_focused(attached):
        mark_new_seen = True

  if mark_new_seen:
    for it in new_items:
      seen_map[it["key"]] = now
    need_save = True

  if need_save:
    _save_seen_keys(state_path, seen_map)


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness tmux-status",
      description=(
          "Emit coding-agent attention summary for the tmux status line."
      ),
  )
  p.add_argument(
      "--tmux-socket",
      metavar="PATH",
      help="Connect to tmux socket at PATH (tmux -S PATH).",
  )
  p.add_argument(
      "--max-width",
      type=int,
      default=32,
      metavar="N",
      help="Maximum visible width of the session-name segment (default: 32).",
  )
  p.add_argument(
      "--plain",
      action="store_true",
      help="Emit plain visible text without tmux #[...] style tags.",
  )
  p.add_argument(
      "--no-notify",
      action="store_true",
      help="Disable OSC 777 notifications for newly urgent prompts.",
  )
  return p.parse_args(argv)


def reconcile_tmux_context_options(
    tmux: TmuxRunner,
    limits_enabled: bool,
    ctx_panes: Sequence[tuple[str, str, Mapping[str, Any], str]],
) -> None:
  """Applies minimal batched tmux updates for per-pane context badges."""
  if not ctx_panes:
    return

  cmds: list[list[str]] = []
  desired_pad = context.TMUX_CONTEXT_PAD if limits_enabled else ""
  if any(p.get("context_pad", "") != desired_pad for _, _, p, _ in ctx_panes):
    if desired_pad:
      cmds.append(["set-option", "-g", "@code_harness_context_pad", desired_pad])
    else:
      cmds.append(["set-option", "-gqu", "@code_harness_context_pad"])

  migrated_sessions: set[str] = set()
  for sname, _, pane, _ in ctx_panes:
    if sname in migrated_sessions:
      continue
    s_left = pane.get("status_left", "")
    if s_left.endswith("#{@code_harness_context} ") and (
        "#{?@code_harness_context," not in s_left
    ):
      migrated_sessions.add(sname)
      # `set-option -t` parses target-pane syntax, so `=name:` matches exact
      # session rather than bare `=name` (which tmux rejects).
      sess_target = f"={sname}:"
      cmds.append(["set-option", "-qu", "-t", sess_target, "status-left"])
      cmds.append(
          ["set-option", "-qu", "-t", sess_target, "status-left-length"]
      )
      cmds.append(
          ["set-option", "-qu", "-t", sess_target, "@code_harness_auto_status"]
      )

  cleaned_windows: set[tuple[str, str]] = set()
  for sname, win_idx_str, pane, desired in ctx_panes:
    if pane.get("context_badge", "") == desired:
      continue
    pid = pane["pane_id"]
    if desired:
      cmds.append(
          ["set-option", "-p", "-t", pid, "@code_harness_context", desired]
      )
    else:
      for opt in (
          "@code_harness_context",
          "@code_harness_context_conv",
          "@code_harness_context_tier",
      ):
        cmds.append(["set-option", "-qup", "-t", pid, opt])
    wkey = (sname, win_idx_str)
    if wkey not in cleaned_windows:
      cleaned_windows.add(wkey)
      for opt in (
          "@code_harness_context",
          "@code_harness_context_conv",
          "@code_harness_context_tier",
      ):
        cmds.append(["set-option", "-quw", "-t", pid, opt])

  if cmds:
    batch: list[str] = []
    for idx, cmd in enumerate(cmds):
      if idx > 0:
        batch.append(";")
      batch.extend(cmd)
    tmux.run(*batch, check=False)


def _default_review_db_path() -> pathlib.Path:
  env_db = os.environ.get("REVIEW_DB", "").strip()
  if env_db:
    return pathlib.Path(os.path.expanduser(env_db))
  return pathlib.Path.home() / "projects" / "code-review" / "reviews.db"


def load_review_targets_by_conv(
    conv_ids: Sequence[str],
    db_path: pathlib.Path | str | None = None,
) -> dict[str, str]:
  """Returns `{conv_id: canonical_target}` from `reviews.db` read-only."""
  unique_ids = sorted({cid for cid in conv_ids if cid})
  if not unique_ids:
    return {}
  target_db = db_path if db_path is not None else _default_review_db_path()
  if not target_db or not pathlib.Path(target_db).is_file():
    return {}
  try:
    uri = f"{pathlib.Path(target_db).resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=0.5)
  except (OSError, sqlite3.Error):
    return {}
  try:
    placeholders = ",".join("?" for _ in unique_ids)
    rows = con.execute(
        f"SELECT conversation_id, target FROM reviews "
        f"WHERE conversation_id IN ({placeholders}) "
        "ORDER BY COALESCE(last_opened_at, updated_at) DESC",
        unique_ids,
    ).fetchall()
    out: dict[str, str] = {}
    for cid, target in rows:
      if cid and target and cid not in out:
        out[cid] = str(target).strip()
    return out
  except sqlite3.Error:
    return {}
  finally:
    con.close()


def resolve_candidate_window_titles(
    title_candidates: Sequence[tuple[str, str, Mapping[str, Any], str, str]],
    config: HarnessConfig | None = None,
    db_path: pathlib.Path | str | None = None,
) -> list[tuple[str, str, Mapping[str, Any], str, bool]]:
  """Resolves desired compact `@code_harness_title` values for CLI panes."""
  if not title_candidates:
    return []
  conv_ids = [conv_id for _, _, _, _, conv_id in title_candidates if conv_id]
  review_targets = load_review_targets_by_conv(conv_ids, db_path=db_path)

  by_dir: dict[str, list[str]] = {}
  for _, _, _, cli_dir, conv_id in title_candidates:
    by_dir.setdefault(cli_dir, []).append(conv_id)

  structured_by_dir: dict[str, dict[str, str]] = {}
  fallback_by_dir: dict[str, dict[str, str]] = {}
  for cli_dir, ids in by_dir.items():
    structured = conversation_labels(
        cli_dir, ids, max_len=0, config=config, include_user_input=False
    )
    structured_by_dir[cli_dir] = structured or {}
    missing = [
        cid
        for cid in ids
        if cid not in structured_by_dir[cli_dir] and cid not in review_targets
    ]
    fallback_by_dir[cli_dir] = (
        conversation_labels(
            cli_dir, missing, max_len=0, config=config, include_user_input=True
        )
        or {}
        if missing
        else {}
    )

  resolved: list[tuple[str, str, Mapping[str, Any], str, bool]] = []
  for sname, win_idx_str, pane, cli_dir, conv_id in title_candidates:
    raw = structured_by_dir.get(cli_dir, {}).get(conv_id, "")
    review_target = review_targets.get(conv_id, "")
    chosen = ""
    if review_target:
      if raw and not is_generic_review_title(raw):
        chosen = raw
      else:
        chosen = f"Review {review_target}"
    else:
      raw_low = str(raw or "").strip(".:!?\"' ").lower()
      is_non_review_blank = (
          not raw
          or raw_low in ("new conversation", "untitled")
          or raw_low.startswith((
              "/start-code-review",
              "/review-github-pr",
              "/review-pr",
          ))
      )
      if not is_non_review_blank:
        chosen = raw
      else:
        fb = fallback_by_dir.get(cli_dir, {}).get(conv_id, "")
        if fb and not is_generic_review_title(fb):
          chosen = fb
    desired = compact_window_title(chosen, max_len=22) if chosen else ""
    resolved.append((sname, win_idx_str, pane, desired, True))
  return resolved


def reconcile_tmux_title_options(
    tmux: TmuxRunner,
    title_panes: Sequence[tuple[str, str, Mapping[str, Any], str, bool]],
    default_cli_names: set[str] | frozenset[str] | None = None,
) -> None:
  """Applies minimal batched tmux updates for per-pane window titles."""
  if not title_panes:
    return
  heal_names = (
      default_cli_names
      if default_cli_names is not None
      else frozenset({"cli", "agy", "antigravity-cli"})
  )
  pane_cmds: list[list[str]] = []
  win_actions: dict[tuple[str, str], tuple[str, str]] = {}
  for sname, win_idx_str, pane, desired, is_cli_pane in title_panes:
    if not pane.get("has_title_fields", False):
      continue
    pid = pane["pane_id"]
    cur = pane.get("code_harness_title", "")
    changed = cur != desired
    if changed:
      if desired:
        pane_cmds.append(
            ["set-option", "-p", "-t", pid, "@code_harness_title", desired]
        )
      else:
        pane_cmds.append(
            ["set-option", "-qup", "-t", pid, "@code_harness_title"]
        )

    wkey = (sname, win_idx_str)
    auto_val = pane.get("automatic_rename", "")
    wname = pane.get("window_name", "")
    if is_cli_pane and auto_val == "0" and wname in heal_names:
      win_actions[wkey] = (pid, "heal")
    elif changed and auto_val == "1" and wkey not in win_actions:
      win_actions[wkey] = (pid, "refresh")

  cmds = list(pane_cmds)
  for pid, mode in win_actions.values():
    if mode == "heal":
      cmds.append(["set-window-option", "-t", pid, "automatic-rename", "on"])
    else:
      cmds.append(["set-window-option", "-t", pid, "automatic-rename", "off"])
      cmds.append(["set-window-option", "-t", pid, "automatic-rename", "on"])

  if cmds:
    batch: list[str] = []
    for idx, cmd in enumerate(cmds):
      if idx > 0:
        batch.append(";")
      batch.extend(cmd)
    tmux.run(*batch, check=False)


def cmd_tmux_status(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  default_limits = context.load_context_limits(config=config, flavor=flavor)
  limits_enabled = default_limits is not None or (
      flavor is None
      and any(f.context_limits is not None for f in config.flavors.values())
  )
  limits_by_flavor: dict[str | None, dict[str, int] | None] = {
      flavor: default_limits,
  }
  tmux = TmuxRunner(find_tmux_bin(), args.tmux_socket)

  windows = list_tmux_windows_and_panes(tmux)
  if windows is None or not windows:
    return 0

  pane_pids = {p["pane_pid"] for w in windows for p in w["panes"]}
  procs, children_map = (
      load_process_table(config=config, root_pids=pane_pids)
      if pane_pids
      else ({}, {})
  )
  data_dir = default_data_dir(config=config)
  mcp_sigs = None
  self_ignored = None

  perm_sessions: list[str] = []
  perm_seen: set[str] = set()
  ask_sessions: list[str] = []
  ask_seen: set[str] = set()
  urgent_items: list[dict[str, Any]] = []
  plan_candidates: list[dict] = []
  plan_count = 0
  done_candidates: list[dict] = []
  done_count = 0
  work_count = 0
  ctx_panes: list[tuple[str, str, Mapping[str, Any], str]] = []
  title_candidates: list[tuple[str, str, Mapping[str, Any], str, str]] = []
  title_panes: list[tuple[str, str, Mapping[str, Any], str, bool]] = []

  for win_idx, win in enumerate(windows):
    sname = win["session_name"]
    for pane_pos, pane in enumerate(win["panes"]):
      has_ctx = bool(pane.get("has_ctx_fields", False))
      has_title = bool(pane.get("has_title_fields", False))
      cli_proc, cli_is_print = find_pane_cli(
          pane["pane_pid"], procs, children_map, config=config
      )
      if cli_proc is None:
        if has_ctx:
          ctx_panes.append((sname, win["window_index"], pane, ""))
        if has_title:
          title_panes.append((sname, win["window_index"], pane, "", False))
        continue
      if mcp_sigs is None:
        mcp_sigs = load_mcp_commands(data_dir, config=config)
        self_ignored = self_ignored_pids(procs, children_map)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, pane["pane_id"], data_dir=data_dir, config=config
      )
      is_subagent: bool | None = None
      if has_ctx:
        pane_flavor = flavor
        if pane_flavor is None:
          pane_flavor, _ = cli_proc_flavor(cli_proc, config=config)
        if pane_flavor not in limits_by_flavor:
          limits_by_flavor[pane_flavor] = context.load_context_limits(
              config=config, flavor=pane_flavor
          )
        pane_limits = limits_by_flavor[pane_flavor]
        if pane_limits is None or cli_is_print:
          desired = ""
        elif not conv_id:
          desired = context.format_tmux_context_badge(0, pane_limits)
        else:
          ctx_info = context.inspect_conversation_context(
              conv_id,
              data_dir=cli_dir,
              limits=pane_limits,
              config=config,
              flavor=pane_flavor,
              extra_child_ids=open_dbs,
          )
          is_subagent = bool(ctx_info.get("is_subagent"))
          desired = (
              ""
              if is_subagent
              else context.format_tmux_context_badge(
                  ctx_info["tokens"], pane_limits
              )
          )
        ctx_panes.append((sname, win["window_index"], pane, desired))
      if has_title:
        if is_subagent is None and not cli_is_print and conv_id:
          is_subagent = is_subagent_db(cli_dir, conv_id, config=config)
        if cli_is_print or is_subagent or not conv_id:
          title_panes.append((
              sname,
              win["window_index"],
              pane,
              "",
              not cli_is_print and not bool(is_subagent),
          ))
        else:
          title_candidates.append(
              (sname, win["window_index"], pane, cli_dir, conv_id)
          )
      state, reason, last_ts = classify_cli_state_with_ts(
          cli_proc,
          cli_is_print,
          pane["pane_id"],
          conv_id,
          open_dbs,
          procs,
          children_map,
          mcp_sigs,  # type: ignore[arg-type]
          self_ignored,  # type: ignore[arg-type]
          cli_dir,
          config=config,
      )
      cat = classify_ui_category(state, reason, conv_id)
      if cat in ("perm", "ask"):
        tok = urgent_prompt_token(
            cli_dir,
            conv_id,
            cat,
            reason,
            last_ts,
            open_dbs=open_dbs,
            cli_start_epoch=cli_proc.get("start_epoch"),
            config=config,
        )
        urgent_items.append({
            "key": (pane["pane_id"], conv_id or "", cat, tok),
            "category": cat,
            "session_name": sname,
            "reason": reason,
        })
        if cat == "perm":
          if sname not in perm_seen:
            perm_seen.add(sname)
            perm_sessions.append(sname)
        elif sname not in ask_seen:
          ask_seen.add(sname)
          ask_sessions.append(sname)
      elif cat == "plan":
        plan_count += 1
        plan_candidates.append({
            "session_name": sname,
            "category": cat,
            "win_idx": win_idx,
            "pane_pos": pane_pos,
            "recency": candidate_recency(
                cli_dir, conv_id, last_ts, config=config
            ),
        })
      elif cat == "done":
        done_count += 1
        if win.get("bell", False):
          done_candidates.append({
              "session_name": sname,
              "category": cat,
              "win_idx": win_idx,
              "pane_pos": pane_pos,
              "recency": candidate_recency(
                  cli_dir, conv_id, last_ts, config=config
              ),
          })
      elif cat == "work":
        work_count += 1

  reconcile_tmux_context_options(tmux, limits_enabled, ctx_panes)
  title_panes.extend(
      resolve_candidate_window_titles(title_candidates, config=config)
  )
  heal_names = {"cli", "agy", "antigravity-cli"} | {
      b for f in config.flavors.values() for b in f.match_basenames if b
  }
  reconcile_tmux_title_options(tmux, title_panes, default_cli_names=heal_names)

  ask_sessions = [s for s in ask_sessions if s not in perm_seen]

  clients_cache: list[list[dict[str, Any]] | None] = [None]

  def get_clients() -> list[dict[str, Any]] | None:
    if clients_cache[0] is None:
      clients_cache[0] = tmux_notify.list_tmux_clients(tmux)
    return clients_cache[0]

  notify_enabled = (
      not args.no_notify and os.environ.get("CODE_HARNESS_TMUX_NOTIFY") != "0"
  )
  maybe_send_urgent_notifications(
      tmux,
      urgent_items,
      args.tmux_socket,
      notify_enabled,
      get_clients,
  )

  sys.stdout.write(
      format_status_line(
          perm_sessions,
          ask_sessions,
          plan_candidates,
          plan_count,
          done_candidates,
          done_count,
          work_count,
          windows,
          tmux,
          args.tmux_socket,
          args.max_width,
          args.plain,
          get_clients=get_clients,
      )
      + "\n"
  )
  return 0

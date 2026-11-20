"""Render coding-agent CLI attention status for the tmux right status line.

Inspects all tmux panes (using code_harness.snapshot detection logic) and
prints a compact segment showing:
  1. Session name(s) needing attention (permission, then ask or belled done
     ranked by recency and falling back to ctrl-space F next-window order),
     left-truncated to fit --max-width.
  2. Count of done/ask CLIs (always shown, even when 0).
  3. Count of working CLIs (always shown, even when 0).

Settled "done" sessions require tmux's `window_bell_flag` to be set on their
window (cleared natively by tmux once viewed), whereas "perm" and "ask"
prompts remain shown without requiring a bell.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

from code_harness.config import HarnessConfig
from code_harness.snapshot import (
    CLI_STATE_PERMISSION,
    CLI_STATE_QUESTION,
    CLI_STATE_SETTLED,
    TmuxRunner,
    _transcript_path,
    classify_cli_state_with_ts,
    default_data_dir,
    find_pane_cli,
    find_tmux_bin,
    harness_data_dirs,
    load_mcp_commands,
    load_process_table,
    resolve_cli_conversation_and_dir,
    self_ignored_pids,
)

ISO_FRAC_RE = re.compile(r"\.(\d+)(?=[+-]|$)")


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
    if len(parts) != 6:
      continue
    sname, widx, wbell, pidx_s, pane_id, ppid_s = parts
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
          "panes": [],
      }
      by_key[key] = win
      windows.append(win)
    elif wbell == "1":
      win["bell"] = True
    win["panes"].append({
        "index": pidx,
        "pane_id": pane_id,
        "pane_pid": ppid,
    })

  for win in windows:
    win["panes"].sort(key=lambda p: p["index"])
  return windows


def current_window_location(
    tmux: TmuxRunner,
    tmux_socket: str | None = None,
) -> tuple[str | None, str | None]:
  """Returns (session_name, window_index) for active client, or (None, None)."""
  sep = "\x1f"
  c_res = tmux.run(
      "list-clients",
      "-F",
      sep.join(["#{client_session}", "#{client_activity}", "#{client_tty}"]),
      check=False,
  )
  if c_res.returncode == 0 and c_res.stdout.strip():
    best = (-1, "", "")
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


def classify_ui_category(state: str, reason: str, conv_id: str | None) -> str:
  """Maps snapshot CLI state into perm/ask/done/new/work."""
  if state == CLI_STATE_PERMISSION:
    return "perm"
  if state == CLI_STATE_QUESTION:
    return "ask"
  if state == CLI_STATE_SETTLED:
    if not conv_id or reason in (
        "empty prompt (no conversation yet)",
        "empty conversation",
    ):
      return "new"
    return "done"
  return "work"


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
  """Returns comparable recency tuple for a done/ask candidate."""
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
) -> dict:
  """Picks the most recent done/ask candidate, breaking ties via F ordering."""
  max_recency = max(c["recency"] for c in candidates)
  tied = [c for c in candidates if c["recency"] == max_recency]
  if len(tied) == 1:
    return tied[0]

  cur_idx = -1
  if len({c["session_name"] for c in tied}) > 1 or len(
      {c["win_idx"] for c in tied}
  ) > 1:
    cur_sess, cur_win = current_window_location(tmux, tmux_socket=tmux_socket)
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


def format_status_line(
    perm_sessions: list[str],
    done_candidates: list[dict],
    done_count: int,
    work_count: int,
    windows: list[dict],
    tmux: TmuxRunner,
    tmux_socket: str | None,
    max_width: int,
    plain: bool,
) -> str:
  """Formats the tmux status-right coding-agent indicator string."""
  parts: list[str] = []

  def esc(text: str) -> str:
    return text if plain else text.replace("#", "##")

  if perm_sessions:
    vis = truncate_left_with_prefix("! ", " ".join(perm_sessions), max_width)
    if vis:
      if plain:
        parts.append(f"{vis} ")
      else:
        parts.append(f"#[fg=colour203,bold]{esc(vis)}#[nobold] ")
  elif done_candidates:
    winner = pick_done_winner(
        done_candidates, windows, tmux, tmux_socket=tmux_socket
    )
    if winner["category"] == "ask":
      vis = truncate_left_with_prefix("? ", winner["session_name"], max_width)
      if vis:
        if plain:
          parts.append(f"{vis} ")
        else:
          parts.append(f"#[fg=colour220,bold]{esc(vis)}#[nobold] ")
    else:
      vis = truncate_left_with_prefix("", winner["session_name"], max_width)
      if vis:
        if plain:
          parts.append(f"{vis} ")
        else:
          parts.append(f"#[fg=colour78]{esc(vis)} ")

  if plain:
    parts.append(f"● {done_count} ")
    parts.append(f"◐ {work_count} ")
  else:
    if done_count > 0:
      parts.append(f"#[fg=colour78,bold]● {done_count}#[nobold] ")
    else:
      parts.append("#[fg=colour78]● 0 ")
    if work_count > 0:
      parts.append(f"#[fg=colour75,bold]◐ {work_count}#[nobold] #[default]")
    else:
      parts.append("#[fg=colour75]◐ 0 #[default]")

  return "".join(parts)


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
  return p.parse_args(argv)


def cmd_tmux_status(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
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
  done_candidates: list[dict] = []
  done_count = 0
  work_count = 0

  for win_idx, win in enumerate(windows):
    sname = win["session_name"]
    for pane_pos, pane in enumerate(win["panes"]):
      cli_proc, cli_is_print = find_pane_cli(
          pane["pane_pid"], procs, children_map, config=config
      )
      if cli_proc is None:
        continue
      if mcp_sigs is None:
        mcp_sigs = load_mcp_commands(data_dir, config=config)
        self_ignored = self_ignored_pids(procs, children_map)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, pane["pane_id"], data_dir=data_dir, config=config
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
      if cat == "perm":
        if sname not in perm_seen:
          perm_seen.add(sname)
          perm_sessions.append(sname)
      elif cat in ("done", "ask"):
        done_count += 1
        if cat == "ask" or win.get("bell", False):
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

  sys.stdout.write(
      format_status_line(
          perm_sessions,
          done_candidates,
          done_count,
          work_count,
          windows,
          tmux,
          args.tmux_socket,
          args.max_width,
          args.plain,
      )
      + "\n"
  )
  return 0

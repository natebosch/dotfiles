"""Cycle through tmux windows running coding-agent CLIs needing attention.

Modes:
  --permission  Cycle windows where a coding-agent CLI (or live subagent) is
                blocked on a permission check or MCP elicitation popup.
  (default)     Cycle windows matching --permission, or where a coding-agent
                CLI has paused on ask_question or settled waiting for user
                input.

When no matching window (or no other matching window) exists, keeps focus where
it is and shows a brief centered tmux popup toast (visible with `status off`).
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess

from code_harness.config import HarnessConfig
from code_harness.snapshot import (
    CLI_STATE_PERMISSION,
    CLI_STATE_QUESTION,
    CLI_STATE_SETTLED,
    TmuxRunner,
    classify_cli_state,
    default_data_dir,
    find_pane_cli,
    find_tmux_bin,
    load_mcp_commands,
    load_process_table,
    resolve_cli_conversation_and_dir,
    self_ignored_pids,
)


def show_toast(
    tmux: TmuxRunner,
    client: str,
    message: str,
    duration_sec: float = 0.8,
) -> None:
  """Displays a brief centered popup toast (visible even when status is off)."""
  log_path = os.environ.get("CODE_HARNESS_TMUX_TOAST_LOG")
  if log_path:
    try:
      with open(log_path, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    except OSError:
      pass

  popup_args = ["-c", client] if client else []
  width = len(message) + 4
  q_msg = shlex.quote(message)
  shell_cmd = f"printf '\\033[?25l %s' {q_msg}; sleep {duration_sec}"
  popup_cmd = [
      "display-popup",
      *popup_args,
      "-E",
      "-k",
      "-b",
      "rounded",
      "-S",
      "fg=colour166",
      "-w",
      str(width),
      "-h",
      "3",
      shell_cmd,
  ]
  if os.environ.get("CODE_HARNESS_TMUX_SYNC_TOAST") == "1":
    tmux.run(*popup_cmd, check=False)
    return
  try:
    subprocess.Popen(
        [*tmux.base, *popup_cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
  except OSError:
    pass


def list_tmux_windows_and_panes(tmux: TmuxRunner) -> list[dict]:
  sep = "\x1f"
  res = tmux.run(
      "list-panes",
      "-a",
      "-F",
      sep.join([
          "#{session_name}",
          "#{window_index}",
          "#{window_zoomed_flag}",
          "#{window_panes}",
          "#{pane_index}",
          "#{pane_id}",
          "#{pane_pid}",
          "#{pane_active}",
      ]),
      check=False,
  )
  if res.returncode != 0 or not res.stdout.strip():
    return []

  windows: list[dict] = []
  by_key: dict[tuple[str, str], dict] = {}
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 8:
      continue
    sname, widx, wzoom, wpanes, pidx_s, pane_id, ppid_s, pact = parts
    try:
      pidx = int(pidx_s)
      ppid = int(ppid_s)
      num_panes = int(wpanes or "1")
    except ValueError:
      continue
    key = (sname, widx)
    win = by_key.get(key)
    if win is None:
      win = {
          "session_name": sname,
          "window_index": widx,
          "zoomed": wzoom == "1",
          "num_panes": num_panes,
          "panes": [],
      }
      by_key[key] = win
      windows.append(win)
    win["panes"].append({
        "index": pidx,
        "pane_id": pane_id,
        "pane_pid": ppid,
        "active": pact == "1",
    })

  for win in windows:
    win["panes"].sort(key=lambda p: p["index"])
  return windows


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness tmux-next",
      description=(
          "Focus the next tmux window with a coding-agent CLI waiting on a "
          "permission check (--permission) or waiting for user input."
      ),
  )
  p.add_argument(
      "-p",
      "--permission",
      action="store_true",
      help="Only match coding-agent CLIs waiting on a permission check.",
  )
  p.add_argument(
      "--list",
      action="store_true",
      help="List matching panes and states without changing tmux focus.",
  )
  p.add_argument(
      "--tmux-socket",
      metavar="PATH",
      help="Connect to tmux socket at PATH (tmux -S PATH).",
  )
  p.add_argument(
      "client",
      nargs="?",
      default="",
      help="Target tmux client tty (e.g. #{client_tty}).",
  )
  return p.parse_args(argv)


def cmd_tmux_next(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  tmux = TmuxRunner(find_tmux_bin(), args.tmux_socket)

  if args.permission:
    target_states = frozenset({CLI_STATE_PERMISSION})
    empty_msg = "No agent waiting on permission"
    no_other_msg = "No other agent waiting on permission"
  else:
    target_states = frozenset({
        CLI_STATE_PERMISSION,
        CLI_STATE_QUESTION,
        CLI_STATE_SETTLED,
    })
    empty_msg = "No agent waiting for input"
    no_other_msg = "No other agent waiting for input"

  windows = list_tmux_windows_and_panes(tmux)
  if not windows:
    return 0

  pane_pids = {p["pane_pid"] for w in windows for p in w["panes"]}
  procs, children_map = load_process_table(
      config=config, root_pids=pane_pids
  )
  data_dir = default_data_dir(config=config)
  ctx: dict[str, object] = {"mcp_sigs": None, "self_ignored": None}

  def matching_panes_in_window(win: dict) -> list[dict]:
    matches: list[dict] = []
    for pane in win["panes"]:
      cli_proc, cli_is_print = find_pane_cli(
          pane["pane_pid"], procs, children_map, config=config
      )
      if cli_proc is None:
        continue
      if ctx["mcp_sigs"] is None:
        ctx["mcp_sigs"] = load_mcp_commands(data_dir, config=config)
        ctx["self_ignored"] = self_ignored_pids(procs, children_map)
      conv_id, open_dbs, cli_dir = resolve_cli_conversation_and_dir(
          cli_proc, pane["pane_id"], data_dir=data_dir, config=config
      )
      state, reason = classify_cli_state(
          cli_proc,
          cli_is_print,
          pane["pane_id"],
          conv_id,
          open_dbs,
          procs,
          children_map,
          ctx["mcp_sigs"],  # type: ignore[arg-type]
          ctx["self_ignored"],  # type: ignore[arg-type]
          cli_dir,
          config=config,
      )
      if state in target_states:
        matches.append({
            **pane,
            "state": state,
            "reason": reason,
            "conversation_id": conv_id,
        })
    return matches

  if args.list:
    for win in windows:
      for m in matching_panes_in_window(win):
        loc = f"{win['session_name']}:{win['window_index']}.{m['index']}"
        cid = m["conversation_id"] or "(new)"
        print(f"{loc}\t{m['pane_id']}\t{m['state']}\t{cid}\t{m['reason']}")
    return 0

  client = args.client
  display_args = ["-t", client] if client else []
  switch_args = ["-c", client] if client else []

  sep = "\x1f"
  cur_res = tmux.run(
      "display-message",
      *display_args,
      "-p",
      sep.join(["#{session_name}", "#{window_index}", "#{pane_id}"]),
      check=False,
  )
  if cur_res.returncode != 0 or not cur_res.stdout.strip():
    return 0
  cur_parts = cur_res.stdout.rstrip("\r\n").split(sep)
  if len(cur_parts) != 3:
    return 0
  cur_sess, cur_win, cur_pane_id = cur_parts

  total = len(windows)
  cur_idx = -1
  for i, win in enumerate(windows):
    if win["session_name"] == cur_sess and win["window_index"] == cur_win:
      cur_idx = i
      break

  if cur_idx >= 0:
    other_indices = [(cur_idx + step) % total for step in range(1, total)]
  else:
    other_indices = list(range(total))

  for idx in other_indices:
    win = windows[idx]
    matches = matching_panes_in_window(win)
    if not matches:
      continue
    chosen = next((m for m in matches if m["active"]), matches[0])
    target_win = f"={win['session_name']}:{win['window_index']}"
    cmd = ["select-window", "-t", target_win]
    if win["zoomed"] and win["num_panes"] > 1:
      cmd += [";", "resize-pane", "-Z", "-t", target_win]
    if not chosen["active"]:
      cmd += [";", "select-pane", "-t", chosen["pane_id"]]
    if win["session_name"] != cur_sess:
      cmd += [
          ";",
          "switch-client",
          *switch_args,
          "-t",
          f"={win['session_name']}",
      ]
    tmux.run(*cmd, check=False)
    return 0

  if cur_idx < 0:
    show_toast(tmux, client, empty_msg)
    return 0

  cur_w = windows[cur_idx]
  cur_matches = matching_panes_in_window(cur_w)
  if not cur_matches:
    show_toast(tmux, client, empty_msg)
    return 0

  match_ids = [m["pane_id"] for m in cur_matches]
  target_win = f"={cur_w['session_name']}:{cur_w['window_index']}"
  if cur_pane_id not in match_ids:
    cmd = []
    if cur_w["zoomed"] and cur_w["num_panes"] > 1:
      cmd += ["resize-pane", "-Z", "-t", target_win, ";"]
    cmd += ["select-pane", "-t", cur_matches[0]["pane_id"]]
    tmux.run(*cmd, check=False)
    return 0

  if len(cur_matches) > 1:
    next_m = cur_matches[(match_ids.index(cur_pane_id) + 1) % len(cur_matches)]
    cmd = []
    if cur_w["zoomed"] and cur_w["num_panes"] > 1:
      cmd += ["resize-pane", "-Z", "-t", target_win, ";"]
    cmd += ["select-pane", "-t", next_m["pane_id"]]
    tmux.run(*cmd, check=False)
    return 0

  show_toast(tmux, client, no_other_msg)
  return 0

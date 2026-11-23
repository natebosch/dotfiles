"""Yank the active tmux window's coding-agent conversation ID to a tmux buffer."""

from __future__ import annotations

import argparse

from code_harness.config import HarnessConfig
from code_harness.snapshot import (
    TmuxRunner,
    default_data_dir,
    find_pane_cli,
    find_tmux_bin,
    load_process_table,
    resolve_cli_conversation_and_dir,
)
from code_harness.tmux_next import show_toast


def list_active_window_panes(tmux: TmuxRunner, client: str) -> list[dict]:
  """Returns panes in the active window, with the active pane first."""
  sep = "\x1f"
  display_args = ["-t", client] if client else []
  cur_res = tmux.run(
      "display-message",
      *display_args,
      "-p",
      sep.join(["#{session_name}", "#{window_index}", "#{pane_id}"]),
      check=False,
  )
  if cur_res.returncode != 0 or not cur_res.stdout.strip():
    return []
  cur_parts = cur_res.stdout.rstrip("\r\n").split(sep)
  if len(cur_parts) != 3:
    return []
  cur_sess, cur_win, cur_pane_id = cur_parts

  target_win = f"={cur_sess}:{cur_win}"
  res = tmux.run(
      "list-panes",
      "-t",
      target_win,
      "-F",
      sep.join([
          "#{pane_index}",
          "#{pane_id}",
          "#{pane_pid}",
          "#{pane_active}",
      ]),
      check=False,
  )
  if res.returncode != 0 or not res.stdout.strip():
    return []

  panes: list[dict] = []
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 4:
      continue
    pidx_s, pane_id, ppid_s, pact = parts
    try:
      pidx = int(pidx_s)
      ppid = int(ppid_s)
    except ValueError:
      continue
    panes.append({
        "index": pidx,
        "pane_id": pane_id,
        "pane_pid": ppid,
        "active": pane_id == cur_pane_id or pact == "1",
    })

  panes.sort(key=lambda p: (0 if p["active"] else 1, p["index"]))
  return panes


def resolve_active_window_conversation_id(
    tmux: TmuxRunner,
    client: str,
    config: HarnessConfig,
) -> str | None:
  """Resolves the conversation ID for the active tmux window, if any."""
  panes = list_active_window_panes(tmux, client)
  if not panes:
    return None

  pane_pids = {p["pane_pid"] for p in panes}
  procs, children_map = load_process_table(config=config, root_pids=pane_pids)
  data_dir = default_data_dir(config=config)

  for pane in panes:
    cli_proc, _ = find_pane_cli(
        pane["pane_pid"], procs, children_map, config=config
    )
    if cli_proc is None:
      continue
    conv_id, _, _ = resolve_cli_conversation_and_dir(
        cli_proc, pane["pane_id"], data_dir=data_dir, config=config
    )
    if conv_id:
      return conv_id
  return None


def yank_to_tmux_buffer(tmux: TmuxRunner, text: str) -> bool:
  """Pushes text to the tmux paste buffer unless it matches the latest buffer.

  Returns True when a new buffer was created, False when skipped as duplicate.
  """
  latest = tmux.run("show-buffer", check=False)
  if latest.returncode == 0 and latest.stdout == text:
    return False
  tmux.run("set-buffer", "--", text, check=False)
  return True


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness tmux-yank-id",
      description=(
          "Yank the active tmux window's coding-agent conversation ID "
          "into the tmux paste buffer."
      ),
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


def cmd_tmux_yank_id(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  tmux = TmuxRunner(find_tmux_bin(), args.tmux_socket)
  client = args.client

  conv_id = resolve_active_window_conversation_id(tmux, client, config)
  if not conv_id:
    show_toast(tmux, client, "No conversation ID to yank")
    return 0

  if yank_to_tmux_buffer(tmux, conv_id):
    show_toast(tmux, client, f"Yanked {conv_id}")
  else:
    show_toast(tmux, client, f"Already yanked {conv_id}")
  return 0

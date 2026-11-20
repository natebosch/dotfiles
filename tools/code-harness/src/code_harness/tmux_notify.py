"""Send OSC 777 desktop notifications to attached tmux client TTYs."""

from __future__ import annotations

import argparse
import os
import stat
from typing import Any, Mapping, Sequence

from code_harness.config import HarnessConfig
from code_harness.snapshot import TmuxRunner, find_tmux_bin


def sanitize_osc_field(text: object, strip_semicolons: bool = False) -> str:
  """Sanitizes text for safe inclusion inside an OSC 777 field."""
  s = str(text or "")
  s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
  s = "".join(
      ch
      for ch in s
      if not (ord(ch) <= 0x1F or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F)
  )
  if strip_semicolons:
    s = s.replace(";", "")
  return " ".join(s.split())


def format_osc777_notify(title: object, body: object = "") -> str:
  """Formats an hterm-compatible OSC 777 desktop notification sequence."""
  clean_title = sanitize_osc_field(title, strip_semicolons=True)
  clean_body = sanitize_osc_field(body, strip_semicolons=False)
  return f"\033]777;notify;{clean_title};{clean_body}\007"


def list_tmux_clients(tmux: TmuxRunner) -> list[dict[str, Any]]:
  """Returns attached client records from `tmux list-clients`."""
  sep = "\x1f"
  res = tmux.run(
      "list-clients",
      "-F",
      sep.join([
          "#{client_tty}",
          "#{client_flags}",
          "#{client_session}",
          "#{client_activity}",
      ]),
      check=False,
  )
  if res.returncode != 0 or not res.stdout.strip():
    return []

  clients: list[dict[str, Any]] = []
  for line in res.stdout.splitlines():
    parts = line.split(sep)
    if len(parts) != 4:
      continue
    ctty, raw_flags, csess, act_s = parts
    flags = {f.strip() for f in raw_flags.split(",") if f.strip()}
    try:
      act = int(act_s or "0")
    except ValueError:
      act = 0
    clients.append({
        "tty": ctty.strip(),
        "flags": flags,
        "focused": "focused" in flags,
        "session": csess,
        "activity": act,
    })
  return clients


def any_client_focused(clients: Sequence[Mapping[str, Any]]) -> bool:
  """True when any client record has the tmux `focused` flag."""
  return any(bool(c.get("focused")) for c in clients)


def write_osc_to_tty(tty_path: str, payload: str | bytes) -> bool:
  """Writes an escape sequence directly to a /dev/* character device."""
  if not tty_path or not tty_path.startswith("/dev/"):
    return False
  fd = None
  try:
    fd = os.open(tty_path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    if not stat.S_ISCHR(os.fstat(fd).st_mode):
      return False
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    view = memoryview(data)
    written = 0
    while written < len(data):
      n = os.write(fd, view[written:])
      if n <= 0:
        return False
      written += n
    return True
  except OSError:
    return False
  finally:
    if fd is not None:
      try:
        os.close(fd)
      except OSError:
        pass


def send_tmux_notification(
    tmux: TmuxRunner,
    title: object,
    body: object = "",
    if_unfocused: bool = False,
    clients: Sequence[Mapping[str, Any]] | None = None,
) -> int:
  """Sends OSC 777 notification to attached tmux client TTYs."""
  if clients is None:
    clients = list_tmux_clients(tmux)
  attached = [c for c in clients if c.get("tty")]
  if not attached:
    return 0
  if if_unfocused and any_client_focused(attached):
    return 0

  payload = format_osc777_notify(title, body)
  sent = 0
  for tty in dict.fromkeys(str(c["tty"]) for c in attached):
    if write_osc_to_tty(tty, payload):
      sent += 1
  return sent


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness tmux-notify",
      description="Send an OSC 777 notification to attached tmux clients.",
  )
  p.add_argument(
      "--if-unfocused",
      action="store_true",
      help="Skip notification if any attached tmux client is focused.",
  )
  p.add_argument(
      "--tmux-socket",
      metavar="PATH",
      help="Connect to tmux socket at PATH (tmux -S PATH).",
  )
  p.add_argument("title", help="Notification title.")
  p.add_argument("body", nargs="?", default="", help="Notification body.")
  return p.parse_args(argv)


def cmd_tmux_notify(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  tmux = TmuxRunner(find_tmux_bin(), args.tmux_socket)
  send_tmux_notification(
      tmux, args.title, args.body, if_unfocused=args.if_unfocused
  )
  return 0

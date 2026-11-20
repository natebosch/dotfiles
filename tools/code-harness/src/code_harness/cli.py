"""Command-line entry point for code-harness."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Callable, Mapping, NoReturn, Sequence

from code_harness.config import (
    CliNotFoundError,
    ConfigError,
    HarnessConfig,
    load_config,
    resolve_cli_bin,
)
from code_harness.context import cmd_context
from code_harness.overview import cmd_overview
from code_harness.snapshot import cmd_snapshot
from code_harness.tmux_next import cmd_tmux_next
from code_harness.tmux_notify import cmd_tmux_notify
from code_harness.tmux_status import cmd_tmux_status

CommandHandler = Callable[[HarnessConfig, str | None, list[str]], int]


def _cmd_run(
    config: HarnessConfig,
    flavor: str | None,
    args: list[str],
) -> int:
  cli_bin = resolve_cli_bin(config, flavor=flavor)
  os.execvp(cli_bin, [cli_bin, *args])
  return 0


PUBLIC_COMMANDS: dict[str, tuple[str, CommandHandler]] = {
    "overview": (
        "Fuzzy overview of tmux sessions, projects, repos, and agent CLIs",
        cmd_overview,
    ),
    "run": (
        "Launch the configured coding-agent CLI with forwarded args",
        _cmd_run,
    ),
    "snapshot": (
        "Snapshot and close coding-agent tmux sessions, or restore them",
        cmd_snapshot,
    ),
}

# Hidden subcommands are omitted from top-level --help. Register agent-facing
# helpers and low-level plumbing commands here.
HIDDEN_COMMANDS: dict[str, CommandHandler] = {
    "context": cmd_context,
    "tmux-next": cmd_tmux_next,
    "tmux-notify": cmd_tmux_notify,
    "tmux-status": cmd_tmux_status,
}


def _format_help() -> str:
  lines = [
      "usage: code-harness [--flavor NAME] [--builtin] <command> [args...]",
      "",
      "Options:",
      "  --flavor NAME   Select a configured harness flavor",
      "  --builtin       Skip PATH subcommand overrides (code-harness-<cmd>)",
      "  -h, --help      Show this help message and exit",
      "",
      "Commands:",
  ]
  for name, (summary, _) in sorted(PUBLIC_COMMANDS.items()):
    lines.append(f"  {name:<14}  {summary}")
  return "\n".join(lines) + "\n"


def _parse_top_level(
    argv: Sequence[str],
) -> tuple[str | None, bool, bool, str | None, list[str]]:
  """Parses leading options before the subcommand name."""
  flavor: str | None = None
  builtin_only = False
  show_help = False
  idx = 0
  while idx < len(argv):
    tok = argv[idx]
    if tok in ("-h", "--help"):
      show_help = True
      return flavor, builtin_only, show_help, None, []
    if tok == "--builtin":
      builtin_only = True
      idx += 1
      continue
    if tok == "--flavor":
      if idx + 1 >= len(argv):
        raise ConfigError("--flavor requires a value")
      flavor = argv[idx + 1]
      idx += 2
      continue
    if tok.startswith("--flavor="):
      flavor = tok.split("=", 1)[1]
      if not flavor:
        raise ConfigError("--flavor requires a non-empty value")
      idx += 1
      continue
    if tok.startswith("-"):
      raise ConfigError(f"Unknown option: {tok}")
    return flavor, builtin_only, False, tok, list(argv[idx + 1 :])
  return flavor, builtin_only, True, None, []


def _find_path_override(
    subcmd: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
  env_map = os.environ if env is None else env
  if env_map.get("CODE_HARNESS_NO_PLUGINS") == "1":
    return None
  return shutil.which(f"code-harness-{subcmd}", path=env_map.get("PATH"))


def main(argv: Sequence[str] | None = None) -> int:
  args = list(sys.argv[1:] if argv is None else argv)
  try:
    flavor, builtin_only, show_help, subcmd, rest = _parse_top_level(args)
    if show_help or subcmd is None:
      sys.stdout.write(_format_help())
      return 0

    if not builtin_only:
      override = _find_path_override(subcmd)
      if override is not None:
        if flavor is not None:
          os.environ["CODE_HARNESS_FLAVOR"] = flavor
        os.execvp(override, [override, *rest])
        return 0

    config = load_config()
    if subcmd in PUBLIC_COMMANDS:
      _, handler = PUBLIC_COMMANDS[subcmd]
      return handler(config, flavor, rest)
    if subcmd in HIDDEN_COMMANDS:
      return HIDDEN_COMMANDS[subcmd](config, flavor, rest)

    sys.stderr.write(f"code-harness: unknown command: {subcmd}\n")
    sys.stderr.write("Run 'code-harness --help' for usage.\n")
    return 2
  except (ConfigError, CliNotFoundError) as exc:
    sys.stderr.write(f"code-harness: {exc}\n")
    return 1


def _cli_entry() -> NoReturn:
  raise SystemExit(main())


if __name__ == "__main__":
  _cli_entry()

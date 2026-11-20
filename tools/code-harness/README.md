# code-harness

`code-harness` is a CLI wrapper and automation entry point for coding-agent
harnesses. By default it targets `agy`, with configuration and override seams
so another harness flavor can be layered in from machine-local config without
changing this repository.

## Usage

```sh
code-harness [--flavor NAME] [--builtin] <command> [args...]
```

- `code-harness run [args...]` resolves the active flavor's CLI binary and
  `exec`s it with all trailing arguments forwarded verbatim. In zsh, `c` is
  aliased to `code-harness run`.
- `code-harness snapshot [options]` scans active tmux sessions for configured
  coding-agent CLI processes, checks whether each conversation has settled,
  writes an executable restore script under
  `${XDG_STATE_HOME:-~/.local/state}/code-harness-tmux/latest.sh`, and closes
  cleanly (or restores with `--restore`).
- `--flavor NAME` (or `CODE_HARNESS_FLAVOR`) selects a configured flavor.
- `--builtin` (or `CODE_HARNESS_NO_PLUGINS=1`) skips `PATH` subcommand
  overrides and invokes the built-in command handler.

This package also installs `git-enhance` on `PATH` (invoked as `git enhance`)
for configuring per-repo dev shells and `.agents/skills/` hardlinks.

## Layering in another harness

Keep harness-specific names, paths, and defaults out of this repository. There
are two seams, ordered from preferred to most general:

### 1. Declarative flavor profiles (preferred)

`code-harness` loads the first config file found from:

1. `$CODE_HARNESS_CONFIG`
2. `${XDG_CONFIG_HOME:-~/.config}/code-harness/config.json`
3. `~/.code-harness/config.json`

A local config file can define additional flavors, change `default_flavor`,
and extend built-in flavors or global flag lists using `extra_*` keys without
duplicating the built-in `agy` definition:

```json
{
  "default_flavor": "custom",
  "flavors": {
    "custom": {
      "env_bin": "CUSTOM_CLI_BIN",
      "candidate_bins": ["/path/to/custom-cli", "custom-cli"],
      "data_dir_name": "custom-cli",
      "match_basenames": ["custom-cli"],
      "match_path_substrings": ["/path/to/custom/"],
      "match_app_data_basenames": ["custom-cli"]
    },
    "agy": {
      "extra_match_path_substrings": ["/local/build/path"]
    }
  },
  "extra_non_tui_subcommands": ["local-subcmd"],
  "extra_cli_takes_value": ["--local-flag"]
}
```

Use declarative config for all path, binary-name, process-matching, and
default-setting differences. Multi-flavor machines (running `agy` and another
flavor concurrently) are handled generically by iterating over configured
flavors rather than hardcoding flavor names.

### 2. `$PATH` subcommand overrides

Before dispatching to a built-in command, `code-harness <subcmd>` checks for
an executable named `code-harness-<subcmd>` on `PATH` and `exec`s it if found.

Reserve `$PATH` overrides for:

- Machine-local subcommands that do not belong in the public repository.
- Coarse behavioral overrides where a subcommand's logic genuinely diverges
  from the built-in implementation.

Guidelines for `$PATH` overrides:

- If an override wraps the built-in command rather than replacing it, invoke
  `code-harness --builtin <subcmd>` (or set `CODE_HARNESS_NO_PLUGINS=1`) to
  avoid recursion.
- Do not call a `$PATH` subcommand inside a per-item loop (such as per-PID or
  per-pane scanning); pass or return batch JSON so an invocation pays at most
  one subprocess boundary.

## Agent-facing and plumbing tools (hidden subcommands)

Tools meant to be called by coding agents (or low-level plumbing helpers used
by higher-level commands, hooks, or tmux scripts) should be implemented as
**hidden subcommands** of `code-harness` rather than standalone scripts in
`bin/`:

- Register them in `HIDDEN_COMMANDS` in `src/code_harness/cli.py` so they are
  omitted from top-level `--help` output.
- They automatically share config loading, flavor resolution, Nix dependency
  isolation, and unit testing under `tools/code-harness/`.
- Because dispatch checks `code-harness-<subcmd>` on `PATH` before
  `HIDDEN_COMMANDS`, a local environment can still override any hidden
  subcommand if needed.

## Development

Stage new files with `git add` before running Nix commands:

```sh
nix build .#code-harness
nix flake check
```

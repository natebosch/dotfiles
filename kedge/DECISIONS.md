# Decision Log

This document records significant pivots, mistakes, and reconsiderations made during the development of `kedge`. Decisions that worked on the first try or were ultimately automated by linting are excluded.

## Kedge ID Separator (`:` vs `_`)
- **Initial Plan:** The user proposed using a colon (`:`) as the separator for Kedge IDs (e.g., `R:repo-name`).
- **The Problem:** When implementing `kedge launch`, we discovered that `tmux` handles colons in session names poorly (often interpreting them as window/pane separators or converting them to newlines).
- **The Pivot:** We updated the entire Kedge ID formatting and parsing logic to use underscores (`_`) instead, resulting in IDs like `R_repo-name`.

## Restricting `.` in Kedge Names
- **Initial Plan:** Kedge names were allowed to contain alphanumeric characters, hyphens, underscores, and dots (`.`).
- **The Problem:** `tmux` does not support dots in session names. To compensate, we initially added complex mapping logic (`TmuxSessionName()`) to dynamically replace dots with underscores when interacting with `tmux`.
- **The Pivot:** The user correctly identified that this dynamic mapping was unnecessary complexity. We pivoted by removing `.` from the allowed characters in Kedge names entirely, ensuring the Kedge ID perfectly mirrors the `tmux` session name 1:1.

## Implicit Session Handling
- **Initial Plan:** We assumed all `kedge`-managed sessions would strictly adhere to the internal ID formatting (e.g., parsing `S_some-session`).
- **The Problem:** When scanning active `tmux` sessions via `SessionSource`, any pre-existing sessions not created by `kedge` (e.g., named `my-backend`) would fail to parse and be ignored or mishandled.
- **The Pivot:** We updated `ParseKedgeID` to be tolerant of arbitrary strings. If a string doesn't start with a known prefix (`R_`, `P_`, `G_`), it is now implicitly treated as a Session-type Kedge (`S_`). This allows `kedge` to seamlessly adopt and manage existing `tmux` sessions.

## Project Name Validation (`kedge project start`)
- **Initial Plan:** User requested alphanumeric and potentially other characters.
- **The Decision:** We settled on a strict regex `^[a-zA-Z0-9_-]+$` (alphanumeric, dashes, and underscores only). This ensures compatibility with filesystem paths and potentially other tools without needing complex escaping or mapping, mirroring the decision made for Kedge IDs.

## TOML Library for `kedge.toml`
- **Initial Plan:** User requested a dependency for TOML formatting/parsing to handle description escaping safely.
- **The Choice:** We chose `github.com/pelletier/go-toml/v2`. It provides modern, fast, and idiomatic struct mapping for Go, ensuring that freeform descriptions in `kedge project start` are correctly serialized into the `kedge.toml` file.

## Git Worktree Branch Management (`kedge project usegitrepo`)
- **Initial Plan:** Add a git worktree for a repo at `~/projects/<name>/r/<repo_name>`.
- **The Problem:** If the branch matching the project name is already checked out in another worktree (including the main repo), `git worktree add` will fail.
- **The Pivot:** We implemented "branch juggling". If the project branch is already checked out:
    1. Try to move that checkout to the "main" branch (`main` or `master`) if "main" is not checked out anywhere else.
    2. If "main" is also checked out elsewhere, create a backup branch named `<project>-kedgebk` to host the previous checkout.
    3. If the `-kedgebk` branch also exists, we fail safely to avoid data loss.
- **Nix Compatibility:** We discovered that running hermetic git tests during a Nix build requires `git` to be explicitly added to `nativeBuildInputs` in `flake.nix`, as it's not present in the default build sandbox.

## Process Replacement for "Tail Call" Subprocesses
- **Context:** During an agentic code review, it was noted that running external subcommands (`kedge-*`) via `exec.Command().Run()` left the parent `kedge` process sitting idly in the process tree.
- **The Decision:** We updated these execution paths to use `syscall.Exec`, which replaces the current binary image entirely with the target binary (a standard pattern for POSIX wrappers).
- **The Learning:** The user subsequently pointed out that this exact same logic applied to `tmux attach` (the final action of `kedge launch`). Moving forward, any subprocess execution that acts as a "tail call" (the very last thing a command does before exiting) should be strongly considered for `syscall.Exec` rather than spawning a child process.

## Zero-Clutter System Audit Logging
- **Context:** We needed a standard-compliant way to capture write-effects (like directory creation or git commits) and silenced failures (like malformed git summaries) without cluttering the host filesystem.
- **The Decision:** We implemented a package-level `slog.Logger` that writes structured JSON logs directly to the Linux standard `$XDG_RUNTIME_DIR` (e.g., `/run/user/$UID/kedge.log`). 
- **Retention:** Because `XDG_RUNTIME_DIR` is a tmpfs wiped automatically on system boot, it guarantees zero long-term clutter while perfectly preserving session-level context. A 5MB manual rotation threshold prevents RAM exhaustion.


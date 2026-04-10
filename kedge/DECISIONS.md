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

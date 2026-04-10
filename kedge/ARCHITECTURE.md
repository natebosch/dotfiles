# Kedge Architecture

## Overview
`kedge` is structured as a single binary with built-in subcommands (via `cobra`), supplemented by the ability to discover and execute external plugins (executables in `$PATH` named `kedge-*`).

The codebase follows standard Go project layout conventions, dividing responsibilities between the command-line interface (`cmd/`) and core, reusable domain logic (`internal/`).

## Directory Structure

### `cmd/`
Contains the CLI entry points and wiring for all built-in commands.
- **`root.go`**: Defines the base `kedge` command and handles dynamic discovery of external `$PATH` executables.
- **`project.go`, `project_start.go`, `project_notes.go`**: Implementation of `kedge project` and its subcommands `start` (interactive project creation) and `notes` (accessing project-specific NOTES.md).
- **Built-in Commands** (`list.go`, `summary.go`, `fuzzypick.go`, `launch.go`, `fuzzylaunch.go`): Thin wrappers that parse arguments, instantiate core domain objects, and invoke business logic.
- **`helpers.go`**: Shared logic for the commands, such as wiring up `fzf` and `tmux` flows.

### `internal/`
Contains private packages that encapsulate the business logic and external service integrations.

#### `internal/kedge` (Core Domain)
The heart of the application, defining the ubiquitous language and data models.
- **`KedgeID`**: A struct representing a parsed Kedge (Type and Name). Handles validation and formatting.
- **`KedgeSource` Interface**: Defines how to discover kedges, read their summaries, and generate tmux configuration info.
- **Implementations**: `GitRepoSource`, `ProjectSource`, and `SessionSource` implement `KedgeSource` for their respective domains.

#### `internal/tmux` (External Wrapper)
Provides a mockable interface (`Tmux`) around the `tmux` CLI.
- Isolates side-effect-producing shell commands (e.g., `tmux ls`, `tmux new-session`).
- Enables unit testing of the `kedge` command logic without requiring a real `tmux` server.

#### `internal/fzf` (External Wrapper)
Provides a mockable interface (`Fzf`) around the `fzf` fuzzy finder.
- Handles standard input/output piping required for interactive terminal UI selection.

## Data Flow
1. **Discovery**: `cmd/list` or `cmd/fuzzypick` requests kedges from all `KedgeSource` implementations.
2. **Formatting**: `KedgeID`s are formatted consistently (e.g., `R_my-repo`) for display and selection.
3. **Execution**: `cmd/launch` receives a Kedge ID, asks the corresponding `KedgeSource` for working directory and environment variables, and delegates session creation/attachment to the `tmux` wrapper.

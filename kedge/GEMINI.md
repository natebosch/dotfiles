# Kedge CLI Development Guidelines

> [!IMPORTANT]
> You **MUST** read `kedge/ARCHITECTURE.md` before planning, investigating, or beginning any code changes.

## Go Dependency Management
- **Direct Dependencies:** Always ensure direct dependencies like `github.com/spf13/cobra` are properly reflected in `go.mod` without the `// indirect` comment.
- **Tidying:** After adding, removing, or updating imports, you MUST run `go mod tidy` from the `kedge/` directory to synchronize `go.mod` and `go.sum`.
- **Integrity:** Never manually remove `// indirect` comments; let `go mod tidy` manage the dependency graph automatically.
- **Nix Vendor Hash:** When adding or updating Go dependencies, the `vendorHash` in the root `flake.nix` MUST be updated. Run `nix flake check` to find the correct new hash.

## Planning & Documentation
- **Implementation Plans:** When creating implementation plans (e.g., in Plan Mode), you MUST explicitly include:
    - Steps to implement comprehensive (but hermetic) unit testing.
    - A final step to run go tests, go lint checks, and nix flake check to test the build and integration tests.
    - Steps to update `kedge/ARCHITECTURE.md` and `kedge/DECISIONS.md` to reflect any architectural changes or significant design decisions.
    - Steps to add dynamic autocomplete for any CLI arguments that accept a fixed set of values.
- **Decision Log:** Use `kedge/DECISIONS.md` to record pivots, mistakes, and choices between competing designs.
    - **Workflow Rule:** If you are in a "fix a bug or review comment" workflow that involves going back and forth with the user or an external agent, treat this as a strong hint to update `DECISIONS.md` with the resolution. Always double-check if a code review comment or fix should apply to other similar patterns in the codebase before concluding.
- **Architecture:** Keep `kedge/ARCHITECTURE.md` up to date as the ground truth for the project's structure and data flow.

## Code Style
- **Avoid Shadowing:** Be vigilant about shadowing `err` variables, especially in nested scopes or after multi-return calls. Use more specific names like `errStat`, `errMkdir`, or `errRel` to maintain clarity and satisfy `govet`.
- **Linting Directives:** When adding `//nolint` comments, you MUST provide a brief explanation on the same line (e.g., `//nolint:gosec // branch and repoDir are controlled internally`) to satisfy `nolintlint`.
- **Descriptive Error Messages:** When wrapping errors, ensure the resulting message provides context about *where* and *why* the failure occurred (e.g., `fmt.Errorf("git worktree list failed in %s: %w (output: %s)", repoDir, err, out)`).
- **Constants for Magic Strings:** Use constants for frequently used magic strings, such as branch names (`main`, `master`), to improve maintainability and satisfy `goconst`.
- **File Permissions:** When using `os.WriteFile` or `os.MkdirAll`, prefer restrictive permissions like `0600` or `0700` unless broader access is explicitly required, to satisfy `gosec`.

## Testing & Quality
- **Frequent Execution:** Run tests and linters frequently after making changes to ensure everything continues to work.
- **Go Tests:** Run `go test ./...` from the `kedge/` directory after completing Go changes for fast local feedback.
- **Linting:** Run `golangci-lint run` from the `kedge/` directory. If `golangci-lint` is not installed, use `nix shell nixpkgs#golangci-lint -c golangci-lint run`.
- **Nix Integration Tests:** Run `nix flake check` from the root directory after completing an arc of work or after Nix changes. This is the final source of truth for a successful change.

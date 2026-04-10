# Kedge CLI Development Guidelines

## Go Dependency Management
- **Direct Dependencies:** Always ensure direct dependencies like `github.com/spf13/cobra` are properly reflected in `go.mod` without the `// indirect` comment.
- **Tidying:** After adding, removing, or updating imports, you MUST run `go mod tidy` from the `kedge/` directory to synchronize `go.mod` and `go.sum`.
- **Integrity:** Never manually remove `// indirect` comments; let `go mod tidy` manage the dependency graph automatically.
- **Nix Vendor Hash:** When adding or updating Go dependencies, the `vendorHash` in the root `flake.nix` MUST be updated. Run `nix flake check` to find the correct new hash.

## Planning & Documentation
- **Implementation Plans:** When creating implementation plans (e.g., in Plan Mode), you MUST explicitly include:
    - Steps to implement comprehensive (but hermetic) unit testing.
    - A final step to run `nix flake check` from the root directory to verify the Nix build and integration tests.
    - Steps to update `kedge/ARCHITECTURE.md` and `kedge/DECISIONS.md` to reflect any architectural changes or significant design decisions.
    - Steps to add dynamic autocomplete for any CLI arguments that accept a fixed set of values.
- **Decision Log:** Use `kedge/DECISIONS.md` to record pivots, mistakes, and choices between competing designs.
- **Architecture:** Keep `kedge/ARCHITECTURE.md` up to date as the ground truth for the project's structure and data flow.

## Testing & Quality
- **Frequent Execution:** Run tests and linters frequently after making changes to ensure everything continues to work.
- **Go Tests:** Run `go test ./...` from the `kedge/` directory after completing Go changes for fast local feedback.
- **Linting:** Run `golangci-lint run` from the `kedge/` directory. If `golangci-lint` is not installed, use `nix shell nixpkgs#golangci-lint -c golangci-lint run`.
- **Nix Integration Tests:** Run `nix flake check` from the root directory after completing an arc of work or after Nix changes. This is the final source of truth for a successful change.

# Kedge CLI Development Guidelines

## Go Dependency Management
- **Direct Dependencies:** Always ensure direct dependencies like `github.com/spf13/cobra` are properly reflected in `go.mod` without the `// indirect` comment.
- **Tidying:** After adding, removing, or updating imports, you MUST run `go mod tidy` from the `kedge/` directory to synchronize `go.mod` and `go.sum`.
- **Integrity:** Never manually remove `// indirect` comments; let `go mod tidy` manage the dependency graph automatically.

## Testing & Quality
- **Frequent Execution:** Run tests and linters frequently after making changes to ensure everything continues to work.
- **Go Tests:** Run `go test ./...` from the `kedge/` directory after completing Go changes for fast local feedback.
- **Linting:** Run `golangci-lint run` from the `kedge/` directory. If `golangci-lint` is not installed, use `nix shell nixpkgs#golangci-lint -c golangci-lint run`.
- **Nix Integration Tests:** Run `nix flake check` from the root directory after completing an arc of work or after Nix changes.

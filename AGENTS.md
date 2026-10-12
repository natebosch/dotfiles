# Agents Guide

## CLI tools (`tools/`)

Build new CLI tooling as self-contained Nix packages under `tools/<name>/`
instead of adding loose scripts to `bin/`.

- Each tool directory has a `default.nix` written as a `pkgs.callPackage`
  derivation (`{ writeShellApplication, ... }: ...`).
- A single directory may expose multiple related commands under `$out/bin/`
  (for example, `tools/dart-tools/`).
- Isolate runtimes and dependencies in the derivation (using
  `writeShellApplication`, `python3Packages.buildPythonApplication`,
  `buildGoModule`, etc.) rather than relying on system interpreters or adding
  interpreters to `home.packages`.
- When a tool invokes host binaries (such as `git`, `tmux`, or `dart`),
  prepend its Nix dependencies to `PATH` (`writeShellApplication` or
  `makeWrapper --prefix PATH`) rather than replacing `PATH`.
- Include unit tests in the derivation (`doCheck = true` / `checkPhase`) when
  the tool has non-trivial logic.

## Building and adding to Home Manager

`flake.nix` auto-discovers every `tools/<name>/default.nix` and exposes it as
`packages.<system>.<name>` and as `tools.<name>` in Home Manager modules.

1. Stage new files with `git add` before running Nix commands (flakes ignore
   untracked files).
2. Build and test the package with `nix build .#<name>`.
3. Add `tools.<name>` to `home.packages` in the appropriate `nix-modules/*.nix`
   module (such as `nix-modules/dev.nix` or `nix-modules/cli.nix`). This repo
   targets both `aarch64-darwin` (`mac`) and `x86_64-linux` (`linux`,
   `glinux`), so wire OS-specific tools only into `nix-modules/linux.nix` or
   `nix-modules/darwin.nix`.
4. Format Nix files with `nix fmt` and verify with `nix flake check`.

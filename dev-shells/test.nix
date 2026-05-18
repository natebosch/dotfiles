# Node tests for package:test. xvfb-run is only needed, and only available,
# on Linux.
{ pkgs, lib }:
pkgs.mkShell {
  nativeBuildInputs = [
    pkgs.nodejs_24
  ]
  ++ lib.optional pkgs.stdenv.hostPlatform.isLinux pkgs.xvfb-run;
}

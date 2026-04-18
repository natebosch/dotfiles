{ pkgs, ... }:
let
  status-load = if pkgs.stdenv.isDarwin then
    "sysctl -n vm.loadavg | sed \"s/{ \\(.*\\) }/\\1/\""
  else
    "cut -d\" \" -f1,2,3 /proc/loadavg";

  status-core-count = if pkgs.stdenv.isDarwin then
    "sysctl -n hw.ncpu"
  else
    "grep -c processor /proc/cpuinfo";
in
{
  programs.tmux = {
    enable = true;
    shortcut = "Space"; # prefix C-Space
    keyMode = "vi";
    historyLimit = 10000;
    escapeTime = 0;
    terminal = "xterm-256color";

    extraConfig = ''
      # The base configuration is read from the source file, but we inject
      # the platform-specific status commands here.
      ${builtins.replaceStrings [
        "status-load"
        "status-core-count"
      ] [
        status-load
        status-core-count
      ] (builtins.readFile ../tmux.conf)}
    '';
  };
}

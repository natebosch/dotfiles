{ pkgs, tools, ... }:
{
  programs.go.enable = true;
  home.packages =
    (with pkgs; [
      ripgrep
      shellcheck
      shfmt
      silver-searcher
    ])
    ++ [
      tools.dart-tools
    ];
}

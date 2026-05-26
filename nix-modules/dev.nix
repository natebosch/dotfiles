{ pkgs, ... }:
{
  programs.go.enable = true;
  home.packages = with pkgs; [
    ripgrep
    shellcheck
    shfmt
    silver-searcher
  ];
}

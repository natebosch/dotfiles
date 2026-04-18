{ pkgs, ... }:
{
  programs.go.enable = true;
  home.packages = with pkgs; [
    shellcheck
    silver-searcher
  ];
}

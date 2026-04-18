{ pkgs, ... }:
{
  programs.home-manager.enable = true;
  home.stateVersion = "26.05";

  home.packages = with pkgs; [
    gh
    htop
    jq
    tree
  ];

  home.file.".man".source = ../man;

  fonts.fontconfig.enable = true;
}

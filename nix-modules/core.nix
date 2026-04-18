{ pkgs, ... }:
{
  programs.home-manager.enable = true;
  home.stateVersion = "26.05";

  home.packages = with pkgs; [
    gh
    htop
    jq
    tree

    # Needs `fonts.fontconfig`.
    source-code-pro
  ];

  home.file.".man".source = ../man;

  fonts.fontconfig.enable = true;
}

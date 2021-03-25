{ pkgs, ... }:

let
  tmux = import ./tmux.nix;
  os_packages = with pkgs; if stdenv.isDarwin then [
    gnused
  ] else [
    orpie
  ];
in
{
  programs.home-manager.enable = true;
  home.stateVersion = "21.05";

  home.username = builtins.getEnv "USER";
  home.homeDirectory = builtins.getEnv "HOME";


  home.packages = with pkgs; [
    diffr
    direnv
    fzf
    gh
    highlight
    htop
    jq
    shellcheck
    silver-searcher
    source-code-pro
    tmux
    tree

    python3
    vim_configurable
  ] ++ os_packages;

  fonts.fontconfig.enable = true;

  programs.go.enable = true;
}

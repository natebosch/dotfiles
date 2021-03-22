{ pkgs, ... }:

let
  tmux = import ./tmux.nix;
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
    orpie
    shellcheck
    silver-searcher
    source-code-pro
    tmux
    tree

    python3
    vim_configurable
  ] ++ lib.optionals stdenv.isDarwin [
    gnused
  ];

  fonts.fontconfig.enable = true;

  programs.go.enable = true;
}

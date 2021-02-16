let
  tmux = import ./tmux.nix;
  pkgs = import <nixpkgs> {};
in {
  install = with pkgs; [
    cmake
    diffr
    direnv
    highlight
    htop
    jq
    shellcheck
    silver-searcher
    source-code-pro
    tmux
    tree
    python3
    (vim_configurable.override { python = python3; })
  ];
}

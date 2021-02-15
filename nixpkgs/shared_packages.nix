let
  tmux = import ./tmux.nix;
  pkgs = import <nixpkgs> {};
in {
  install = with pkgs; [
    cmake
    highlight
    htop
    jq
    shellcheck
    silver-searcher
    source-code-pro
    tmux
    tree
    vim
  ];
}

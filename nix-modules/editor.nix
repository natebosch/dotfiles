{ pkgs, ... }:
let
  # vim-plug is still used for plugins not in nixpkgs and local checkouts, see
  # vimrc. The nixpkgs package has plug.vim at the root rather than in
  # autoload/, and start packages aren't on the runtimepath until after the
  # vimrc runs, so put it in an autoload/ dir that is added to the runtimepath
  # before the vimrc.
  vimPlugAutoload = pkgs.runCommand "vim-plug-autoload" { } ''
    mkdir -p $out/autoload
    ln -s ${pkgs.vimPlugins.vim-plug}/plug.vim $out/autoload/plug.vim
  '';
  myVim = pkgs.vim-full.customize {
    name = "vim";
    vimrcConfig.packages.myVimPackage = {
      start = with pkgs.vimPlugins; [
        vim-repeat
        targets-vim
        vim-indent-object
        vim-sort-motion
        vim-css-color
        vim-peekaboo
        vim-textobj-user
        vim-textobj-variable-segment
        traces-vim
        vim-matchup
        vim-tmux-navigator
        vimagit
        vim-fugitive
        vim-rhubarb
        vim-surround
        vim-commentary
        vim-sneak
        delimitMate
        vim-exchange
        ultisnips
        fzf-vim
        vim-eunuch
        vim-tmux
        vim-git
        vim-nix
        vim-openscad
      ];
    };
    # Vim finds the standard library for its embedded python by searching
    # the PATH for `python3`. Without this, a system python found first
    # provides a mismatched standard library and UltiSnips fails to load.
    vimrcConfig.customRC = ''
      set rtp^=${vimPlugAutoload}
      set pythonthreehome=${pkgs.python3}
    ''
    + builtins.readFile ../vimrc;
  };
in
{
  home.packages = [
    myVim
  ];

  home.file = {
    ".vim-extra".source = ../vim-extra;
  };
}

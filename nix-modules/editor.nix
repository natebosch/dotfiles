{ pkgs, ... }:
let
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
        vim-tmux-focus-events
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
    vimrcConfig.customRC = builtins.readFile ../vimrc;
  };
in
{
  home.packages = with pkgs; [
    python3
    myVim
  ];

  home.file = {
    ".vim-extra".source = ../vim-extra;
  };
}

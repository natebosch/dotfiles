{ pkgs, ... }:

let
  tmux = import ./tmux.nix;
  os_packages = with pkgs; if stdenv.isDarwin then [
    gnused
  ] else [
    orpie
  ];
  diffr = (pkgs.writeShellScriptBin "diffr" ''
    ${pkgs.diffr}/bin/diffr \
      --colors \
        refine-added:none:background:0x33,0x99,0x33:foreground:white:bold \
      --colors added:none:background:0x33,0x55,0x33:foreground:white \
      --colors \
        refine-removed:none:background:0x99,0x33,0x33:bold:foreground:white \
      --colors removed:none:background:0x55,0x33,0x33:foreground:white \
      --line-numbers
  '');
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
  home.file = {
    ".vimrc".source = ../vimrc;
    ".zshrc".source = ../zshrc;
    ".gitconfig".source = ../gitconfig;
    # TODO - Move out of $HOME.
    ".zshrc.d".source = ../zshrc.d;
    ".vim-extra".source = ../vim-extra;
    ".gitignore".source = ../gitignore;
    ".bin".source = ../bin;
    ".git-templates".source = ../git-templates;
    ".highlight".source = ../highlight;
    ".man".source = ../man;
    ".tmux.conf".text =
      (builtins.replaceStrings [
        "status-load"
        "status-core-count"
      ] (if pkgs.stdenv.isDarwin then [
        "sysctl -n vm.loadavg | sed \"s/{ \\(.*\\) }\\1\""
        "sysctl -n hw.ncpu"
      ] else [
        "grep -c processor /proc/cpuinfo"
        "cut -d\" \" -f1,2,3 /proc/loadavg"
      ]) (builtins.readFile ../tmux.conf));
  };
}

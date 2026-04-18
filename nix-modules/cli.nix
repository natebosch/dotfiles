{ config, pkgs, ... }:
let
  # Installed as `diffr` so the colors apply everywhere it is used as a pager
  # or preview filter.
  diffrColored = pkgs.writeShellApplication {
    name = "diffr";
    runtimeInputs = [ pkgs.diffr ];
    text = ''
      exec diffr \
        --colors \
          refine-added:none:background:0x33,0x99,0x33:foreground:white:bold \
        --colors added:none:background:0x33,0x55,0x33:foreground:white \
        --colors \
          refine-removed:none:background:0x99,0x33,0x33:bold:foreground:white \
        --colors removed:none:background:0x55,0x33,0x33:foreground:white \
        --line-numbers "$@"
    '';
  };
in
{
  home.packages = [
    diffrColored
    pkgs.highlight
  ];

  programs.zsh = {
    enable = true;
    dotDir = "${config.home.homeDirectory}/.zsh";
    defaultKeymap = "viins";

    history = {
      size = 35000;
      save = 35000;
      path = "${config.home.homeDirectory}/.history";
      extended = true;
      ignoreAllDups = true;
    };

    # Only rebuild the completion dump once a day, a full `compinit` is slow.
    completionInit = ''
      autoload -U compinit
      _zcompdump=(''${ZDOTDIR:-$HOME}/.zcompdump(N.mh-24))
      if (( $#_zcompdump )); then
        compinit -C
      else
        compinit
      fi
      unset _zcompdump
    '';

    initContent = ''
      # The original zshrc content (excluding parts handled by HM). Sets up
      # PATH so it must come before the configurations which depend on it.
      ${builtins.readFile ../zshrc}

      # Source all zsh configurations from the repository
      for config_file (${../zshrc.d}/*); do
        source "$config_file"
      done

      # Source local machine-specific config if it exists
      if [[ -d $HOME/.zshrc.local ]]; then
        for config_file ($HOME/.zshrc.local/*); do
          source "$config_file"
        done
      elif [[ -f $HOME/.zshrc.local ]]; then
        source $HOME/.zshrc.local
      fi
      unset config_file
    '';
  };

  # Handle direnv and fzf through HM modules for idiomatic setup
  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };

  programs.fzf = {
    enable = true;
    defaultCommand = "find_files";
    defaultOptions = [
      "--exact"
      "--inline-info"
    ];
    fileWidgetCommand = "find_files";
    fileWidgetOptions = [ "--preview 'preview {}'" ];
  };

  home.file = {
    ".bin".source = ../bin;
    ".highlight".source = ../highlight;
  };
}

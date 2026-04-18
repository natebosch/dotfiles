{ config, pkgs, ... }:
let
  diffr = (pkgs.writeShellApplication {
    name = "diffr";
    runtimeInputs = [ pkgs.diffr ];
    text = ''
      diffr \
        --colors \
          refine-added:none:background:0x33,0x99,0x33:foreground:white:bold \
        --colors added:none:background:0x33,0x55,0x33:foreground:white \
        --colors \
          refine-removed:none:background:0x99,0x33,0x33:bold:foreground:white \
        --colors removed:none:background:0x55,0x33,0x33:foreground:white \
        --line-numbers "$@"
    '';
  });
in
{
  home.packages = with pkgs; [
    diffr
    highlight
    source-code-pro
  ];

  programs.zsh = {
    enable = true;
    dotDir = "${config.home.homeDirectory}/.zsh";

    history = {
      size = 35000;
      save = 35000;
      path = "${config.home.homeDirectory}/.history";
      extended = true;
      ignoreDups = true;
      ignoreSpace = true;
      share = true;
    };

    # Automatically handles completion, compinit, etc.
    enableCompletion = true;

    initContent = ''
      # Source all zsh configurations from the repository
      for config_file (${../zshrc.d}/*); do
        if [[ "$config_file" != */fzf.zsh ]]; then
          source "$config_file"
        fi
      done

      # Source local machine-specific config if it exists
      if [[ -d $HOME/.zshrc.local ]]; then
        for config_file ($HOME/.zshrc.local/*); do
          source "$config_file"
        done
      elif [[ -f $HOME/.zshrc.local ]]; then
        source $HOME/.zshrc.local
      fi

      # Source the original zshrc content (excluding parts handled by HM)
      ${builtins.readFile ../zshrc}
    '';
  };

  # Handle direnv and fzf through HM modules for idiomatic setup
  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
    enableZshIntegration = true;
  };

  programs.fzf = {
    enable = true;
    enableZshIntegration = true;
    defaultCommand = "find_files";
    defaultOptions = [ "--exact" "--inline-info" ];
    fileWidgetCommand = "find_files";
    fileWidgetOptions = [ "--preview 'preview {}'" ];
  };

  home.file = {
    ".bin".source = ../bin;
    ".highlight".source = ../highlight;
  };
}

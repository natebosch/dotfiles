export FZF_DEFAULT_OPTS="--exact"

export FZF_DEFAULT_COMMAND="find_files || default_find_files"
export FZF_CTRL_T_COMMAND="find_files || default_find_files"

# Fallbacks if fzf isn't available
if [ ! -f ~/.fzf.zsh ]
then
  bindkey "^r" history-incremental-search-backward
fi

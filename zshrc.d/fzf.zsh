export FZF_DEFAULT_OPTS="--exact"

if (( $+commands[ag] )) ; then
  export FZF_DEFAULT_COMMAND="find_files || ag -l --nocolor --nogroup --depth 10 -g ''"
fi

# Fallbacks if fzf isn't available
if [ ! -f ~/.fzf.zsh ]
then
  bindkey "^r" history-incremental-search-backward
fi

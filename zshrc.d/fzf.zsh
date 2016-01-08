if [[ -f ~/.fzf.zsh ]] ; then
  source ~/.fzf.zsh

  if (( $+commands[ag] )) ; then
    export FZF_DEFAULT_COMMAND="ag -l --nocolor --nogroup --depth 10 -g ''"
  fi
else
  bindkey "^r" history-incremental-search-backward
fi

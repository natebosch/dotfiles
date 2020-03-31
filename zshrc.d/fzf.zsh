export FZF_DEFAULT_OPTS="--exact --inline-info \
  --bind=ctrl-d:half-page-down --bind=ctrl-u:half-page-up"

export FZF_DEFAULT_COMMAND="find_files || default_find_files"
export FZF_CTRL_T_COMMAND="find_files || default_find_files"
export FZF_CTRL_T_OPTS="--preview 'preview {}'"
export FZF_PREVIEW_COMMAND="preview {}"

# Fallbacks if fzf isn't available
if [ ! -f ~/.fzf.zsh ]
then
  bindkey "^r" history-incremental-search-backward
fi

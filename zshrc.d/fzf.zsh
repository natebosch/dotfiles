export FZF_DEFAULT_OPTS="--exact"

if (( $+commands[ag] )) ; then
  export FZF_DEFAULT_COMMAND="find_files || ag -l --nocolor --nogroup --depth 10 -g ''"
fi

# ag doesn't work with ctrl_t fzf
local defaultFind
defaultFind="\
command find -L . \\( -path '*/\\.*' -o -fstype 'dev' -o -fstype 'proc' \\) -prune \
      -o -type f -print \
      -o -type d -print \
      -o -type l -print 2> /dev/null | sed 1d | cut -b3-"
export FZF_CTRL_T_COMMAND="find_files || $defaultFind"

# Fallbacks if fzf isn't available
if [ ! -f ~/.fzf.zsh ]
then
  bindkey "^r" history-incremental-search-backward
fi

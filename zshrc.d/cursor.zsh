# Change cursor shape for different vi modes.
function zle-line-init zle-keymap-select {
  if [[ $KEYMAP == vicmd ]]; then
    echo -ne '\e[1 q' # Blink Block
  elif [[ $KEYMAP == main ]] \
    || [[ $KEYMAP == viins ]] \
    || [[ $KEYMAP = '' ]]; then
    echo -ne '\e[2 q' # Solid Block
  fi
}
zle -N zle-line-init
zle -N zle-keymap-select

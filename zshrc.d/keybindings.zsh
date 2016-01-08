#Vi keybindings with a few emacs shortcuts
bindkey -v
bindkey "^p" history-search-backward
bindkey "^n" history-search-forward
bindkey "^a" beginning-of-line
bindkey "^e" end-of-line
bindkey "^i" expand-or-complete-prefix # ctrl-i acts like tab
bindkey -s "^o" "\n" # run a command with ctrl-o
bindkey "^f" forward-char
bindkey "^b" backward-char
bindkey "^w" backward-kill-word
bindkey "^h" backward-delete-char
bindkey "^?" backward-delete-char
bindkey ' ' magic-space  # also do history expansion on space
#AWESOME...
#pushes current command on command stack and gives blank line, after that line
#runs command stack is popped
bindkey "^u" push-line-or-edit

autoload -U edit-command-line
zle -N edit-command-line
bindkey -M vicmd v edit-command-line


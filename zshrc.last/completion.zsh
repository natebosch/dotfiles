## Globbing options
unsetopt extendedglob notify
#setopt EXTENDED_GLOB
setopt NO_CASE_GLOB
setopt NUMERIC_GLOB_SORT


## Completion Options
fpath+=($DOTDIR/.zsh/completion)
fpath=($^fpath(N))

autoload -U compinit
FRESH_DUMP=($DOTDIR/.zcompdump(N.mh-24))
if [[ -n "$FRESH_DUMP" ]]; then
  compinit -C;
else
  compinit;
fi;

setopt COMPLETE_IN_WORD     # Allow tab completion in the middle of a word
setopt CORRECT              # Spell check commands
setopt ALWAYS_TO_END        # Push cursor on completions.

# cache completions
zstyle ':completion:*' use-cache on
zstyle ':completion:*' cache-path $HOME/.zsh/cache

# case insensitive completion when typing with lowercase
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Z}'

# ignore completion functions for missing commands
zstyle ':completion:*:functions' ignored-patterns '_*'

zstyle ':completion:*' menu select
zstyle ':completion:*' verbose yes
zstyle ':completion:*:descriptions' format '%B%d%b'
zstyle ':completion:*:messages' format '%d'
zstyle ':completion:*:warnings' format 'No matches for: %d'
zstyle ':completion:*' group-name ''
zstyle ':completion:*' completer _oldlist _expand _complete
# Have the newer files last so I see them first
zstyle ':completion:*' file-sort modification reverse

zstyle ':completion:*' list-colors ${(s.:.)LS_COLORS}

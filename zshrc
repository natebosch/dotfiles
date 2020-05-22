## Use DOTDIR in other places to locate custom files.
export DOTDIR=${ZDOTDIR:=$HOME} # Use home, override it when ZDOTDIR is set

export PATH=
path=(
  $DOTDIR/.bin.local
  $DOTDIR/.bin
  $DOTDIR/.local/bin
  /usr/local/bin
  /usr/bin
  /bin
  /usr/local/sbin
  /usr/sbin
  /sbin
  /snap/bin
  $DOTDIR/.depot_tools/depot_tools
)
export MANPATH=":$DOTDIR/.man"

fpath+=(
  $DOTDIR/.brew/completions/zsh
  $DOTDIR/.zsh/completion
)

autoload -U compinit
FRESH_DUMP=($DOTDIR/.zcomdump(N.mh-24))
if [[ -n "$FRESH_DUMP" ]]; then
  compinit -C
else
  compinit
fi

## Source all zsh customizations
if [ -d $DOTDIR/.zshrc.d ]
then
    for config_file ($DOTDIR/.zshrc.d/*) source $config_file
fi
if [ -d $DOTDIR/.zshrc.local ]
then
    for config_file ($DOTDIR/.zshrc.local/*) source $config_file
fi
unset config_file

# Clean up non-existent path entries
path=($^path(N))
fpath=($^fpath(N))

setopt NO_BEEP      # Never ever beep. Ever
MAILCHECK=0         # disable mail checking

setopt ZLE          # ZSH line editor
setopt VI

# VI editing mode is a pain to use if you have to wait for <ESC> to register.
# This times out multi-char key combos as fast as possible. (1/100th of a
# second.)
KEYTIMEOUT=1
export EDITOR=$(which vim)
export VISUAL=$EDITOR   # some programs use this instead of EDITOR
export PAGER=less       # less is more :)
# fewer bells, case insensitive searching, status line, and colors
export LESS='-q -i -M -R'

[ ! -f ~/.fzf.zsh ] || source ~/.fzf.zsh

path=(
  ~/.bin.local
  ~/.bin
  $path
)
fpath=(
  ~/.nix-profile/share/zsh/site-functions
  $fpath
)

autoload -U compinit
setopt extendedglob
if [[ -n $HOME/.zcompdump(#qN.mh-24) ]]; then
  compinit -C
else
  compinit
  compdump
fi
unsetopt extendedglob

## Source all zsh customizations
if [ -d $HOME/.zshrc.d ]
then
    for config_file ($HOME/.zshrc.d/*) source $config_file
fi
if [ -d $HOME/.zshrc.local ]
then
    for config_file ($HOME/.zshrc.local/*) source $config_file
fi
unset config_file

# Tools that require their own setup
if [ -n "${commands[fzf-share]}" ]; then
  source "$(fzf-share)/key-bindings.zsh"
  source "$(fzf-share)/completion.zsh"
fi
if [ -e ~/.nix-profile/etc/profile.d/nix.sh ]; then
  source ~/.nix-profile/etc/profile.d/nix.sh
fi
if [ -e /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh  ]; then
  source /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi
# Source home-manager session variables.
# Try the standard nix profile, and the flake-based home-manager profile.
for hm_vars in \
  ~/.nix-profile/etc/profile.d/hm-session-vars.sh \
  ~/.local/state/nix/profiles/home-manager/home-path/etc/profile.d/hm-session-vars.sh
do
  if [ -e "$hm_vars" ]; then
    source "$hm_vars"
    break
  fi
done
unset hm_vars
eval "$(direnv hook zsh)"

# Clean up non-existent and duplicate path entries
typeset -U path
path=($^path(N))
typeset -U fpath
fpath=($^fpath(N))
MANPATH="$HOME/.man:"

setopt NO_BEEP      # Never ever beep. Ever
MAILCHECK=0         # disable mail checking

setopt ZLE          # ZSH line editor
setopt VI

# VI editing mode is a pain to use if you have to wait for <ESC> to register.
# This times out multi-char key combos as fast as possible. (1/100th of a
# second.)
KEYTIMEOUT=1
if [ -n "$VIM" ]; then
  export EDITOR=$(which edit_nested)
  alias e=$(which drop)
else
  export EDITOR=$(which vim)
  alias e=$(which vim)
fi
export VISUAL=$EDITOR

export PAGER=less       # less is more :)
# fewer bells, case insensitive searching, status line, and colors
export LESS='-q -i -M -R'
export BROWSER=yank

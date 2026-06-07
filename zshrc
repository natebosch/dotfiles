path=(
  ~/.bin.local
  ~/.bin
  $path
  ~/.local/bin
)

# Tools that require their own setup
#
# On macOS, nix-darwin sources nix-daemon.sh in /etc/zshenv which sets
# __ETC_PROFILE_NIX_SOURCED and prepends nix paths to $PATH. Then
# /etc/zprofile runs /usr/libexec/path_helper which reorders $PATH,
# burying the nix entries behind system paths. The guard variable
# prevents re-sourcing, so we clear it to let nix-daemon.sh re-prepend
# the nix paths now that path_helper is done.
unset __ETC_PROFILE_NIX_SOURCED
if [ -e ~/.nix-profile/etc/profile.d/nix.sh ]; then
  source ~/.nix-profile/etc/profile.d/nix.sh
fi
if [ -e /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh  ]; then
  source /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi
if [ -e ~/.nix-profile/etc/profile.d/hm-session-vars.sh ]; then
  source ~/.nix-profile/etc/profile.d/hm-session-vars.sh
fi

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

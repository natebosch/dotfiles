export REPORTTIME=20 #don't report time for sourcing this file
## Use DOTDIR in other places to locate custom files.
export DOTDIR=${ZDOTDIR:=$HOME} # Use homedir, but override it when ZDOTDIR is set
export HOSTNAME=`/bin/hostname`

export PATH=
path=(
       $DOTDIR/.bin
       $DOTDIR/bin
       /usr/local/bin
       /usr/bin
       /bin
       /usr/sbin
       /sbin
       /usr/local/sbin
       /usr/X11R6/bin
       /usr/X11/bin
     )
path=($^path(N))
# For paths separated with rather than an array
function path_add() {
    old_ifs="$IFS"
    IFS=":"
    all_args="$*"
    # Remove duplicates
    typeset -U path_dirs
    path_dirs=(${(s/:/)all_args})
    echo "$path_dirs"
    IFS="$old_ifs"
    unset old_ifs,all_args,path_dirs
}
export PYTHONPATH=$(path_add $DOTDIR/.pythonlib $PYTHONPATH)

## Add custom functions
if [ -d $DOTDIR/.fpath ]
then
    fpath=( $DOTDIR/.fpath $fpath )
fi

## Source all zsh customizations
if [ -d $DOTDIR/.zshrc.d ]
then
    for config_file ($DOTDIR/.zshrc.d/*) source $config_file
fi
unset config_file

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
export LESS='-i -M -R'  # case insensitive searching, more detailed status line, and colors in less

clear
export REPORTTIME=1     # Wrap long commands with time
##* I'd like to do this for long wait commands as well,
##  but setting to nonzero only reports command with long CPU time

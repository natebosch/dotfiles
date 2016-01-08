######################### history options ############################
setopt EXTENDED_HISTORY        # store time in history
setopt HIST_EXPIRE_DUPS_FIRST  # unique events are more useful
setopt HIST_VERIFY	           # Make those history commands nice
setopt INC_APPEND_HISTORY      # immediately insert history into history file
HISTSIZE=36000                 # spots for duplicates/uniques
SAVEHIST=35000                 # unique events
HISTFILE=~/.history
#setopt SHARE_HISTORY
setopt APPEND_HISTORY
setopt HIST_FIND_NO_DUPS

# if a line starts with a space, don't save it
setopt HIST_IGNORE_SPACE
setopt HIST_NO_STORE


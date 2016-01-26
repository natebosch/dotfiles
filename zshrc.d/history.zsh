setopt EXTENDED_HISTORY        # store time in history
setopt HIST_IGNORE_ALL_DUPS    # don't insert any duplicates
setopt HIST_IGNORE_SPACE       # don't save commands starting with space
setopt HIST_NO_STORE           # don't save 'history' commands
setopt HIST_VERIFY             # don't execute history subsitutions immediately
setopt INC_APPEND_HISTORY      # immediately insert history into history file
setopt SHARE_HISTORY           # between open shells

HISTSIZE=35000
SAVEHIST=35000
HISTFILE=~/.history



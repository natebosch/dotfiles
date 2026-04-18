setopt HIST_NO_STORE           # don't save 'history' commands
setopt HIST_VERIFY             # don't execute history subsitutions immediately
setopt INC_APPEND_HISTORY      # immediately insert history into history file

function precmd() {
  # Delete unnecessary or annoying history
  local result=$?
  local last=$history[$((HISTCMD-1))]
  if [[
    $result -eq 64 # Bad Usage
    || $result -eq 127 # Command not found
    || ("$last" == echo* && "$last" == *$'\n'*) # Multiline echo
    || ("$last" == git* && $result -eq 129) # Bad git usage
    ]]; then
    # Identify command based on timestamp.
    local stamp="$(fc -l -t %s -1 | awk '{print $2}')"
    # Delete single line command
    sed -i '/^: '"$stamp"':.*[^\]$/d' $HISTFILE
    # Delete multiline command
    sed -i '/^: '"$stamp"':.*\\$/,${1,/[^\]$/d}' $HISTFILE
  fi
}

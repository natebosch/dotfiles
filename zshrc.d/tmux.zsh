alias here='tmux set-option default-path "$PWD"'

s() {
  session=${1:-0} # Default to session named '0'

  # Check TMUX_SESSION to check for a nested loop session
  if [[ -n $TMUX ]] then
    nestedsessions=$(tmux show-environment | grep TMUX_SESSION | cut -d'=' -f2)
    nestedsessions=(${(s/ /)nestedsessions})
    if [[ ${nestedsessions[(i)$session]} -le ${#nestedsessions} ]] then
      echo "Already in session $session"
      exit -1
    fi
  fi
  new_session=$nestedsessions
  new_session+=($session)
  if [[ -n $TMUX ]]
  then
    tmux rename-window $session
  fi
  if tmux has -t $session; then
    #session exists, attach to it and detach other clients
    TMUX_SESSION=$new_session tmux -2 a -t $session -d
  else
    #create the session, force tmux to allow nesting
    TMUX="" tmux -2 new -s $session -d
    if [[ $session == "0" ]]
    then
      tmux source-file $DOTDIR/.tmux-root.conf
    fi
    TMUX="" tmux set-option -t $session default-path "$PWD"
    TMUX_SESSION=$new_session TMUX="" tmux -2 a -t $session -d
  fi
  unset new_session,session,nestedsessions
}
compdef '_arguments "1:tmux session:($(tmux ls -F \#\{session_name\}))"' s

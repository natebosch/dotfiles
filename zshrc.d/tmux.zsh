alias here='tmux set-option default-path "$PWD"'

s() {
  local session
  if (($+1)) then
    session=$1
  else
    session=$(tmux list-sessions -F "#{session_name}" | fzf --exit-0)
  fi

  if [ -z "$session" ]; then
    echo "No session specified!"
    return -1
  fi

  if (! tmux has -t $session) then
    TMUX="" tmux -2 new -s $session -d
    TMUX="" tmux set-option -t $session default-path "$PWD"
  fi

  if [[ -n $TMUX ]] then
    tmux switch-client -t "$session"
  else
    tmux -2 a -t $session -d
  fi
}
compdef '_arguments "1:tmux session:($(tmux ls -F \#\{session_name\}))"' s

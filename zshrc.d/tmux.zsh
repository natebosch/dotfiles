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
  fi

  if [[ -n $TMUX ]] then
    tmux switch-client -t "$session"
  else
    tmux -2 a -t $session -d
  fi
}
compdef '_arguments "1:tmux session:($(tmux ls -F \#\{session_name\}))"' s

change_tmux_pwd() {
  local session_name=$(tmux display-message -p '#S')
  local tmp_session_name="${session_name}-old"
  tmux rename-session $tmp_session_name
  tmux new-session -s $session_name -d
  local has_renumber=$(tmux show-options -g | grep 'renumber-windows on')
  if [[ -z $has_renumber ]]; then
    tmux new-window -t $session_name:99
    tmux kill-window -t $session_name:0
  fi
  local win_id
  for win_id in $(tmux list-windows -F '#I'); do
    if [[ -z $has_renumber ]]; then
      tmux move-window -s $tmp_session_name:$win_id -t $session_name:$win_id
    else
      tmux move-window -s $tmp_session_name:$win_id -t $session_name
    fi
  done
  if [[ -z $has_renumber ]]; then
    tmux kill-window -t $session_name:99
  else
    tmux kill-window -t $session_name:0
  fi
  tmux switch-client -t $session_name
}
alias here=change_tmux_pwd

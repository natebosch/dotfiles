alias s=session_finder
_tmux_sessions() { tmux ls -F "#{session_name}" 2>/dev/null }
compdef '_arguments "1:tmux session:($(_tmux_sessions))"' session_finder

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
if [ -n "$TMUX" ]; then
  alias here=change_tmux_pwd
  alias clear=clear\;tmux\ clear-history
fi

# Set pane title to CWD
function _tmux_update_title() {
  printf '\033]2;%s\033\\' "$PWD"
}
if [ -n "$TMUX_PANE" ]; then
  chpwd_functions+=("_tmux_update_title")
  _tmux_update_title
fi

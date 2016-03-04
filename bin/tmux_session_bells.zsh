#!/usr/bin/zsh

local sessions
local -a bell_sessions
sessions=($(tmux list-sessions -F '#{session_name}'))
for session in $sessions; do
  if [[ -n $(tmux list-windows -t $session | grep '!') ]]; then
    bell_sessions+=$session
  fi
done
echo $bell_sessions

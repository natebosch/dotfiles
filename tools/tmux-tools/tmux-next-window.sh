# Advance to the next window in the current tmux session, or to the first
# window of the next session when on the last window (wrapping around).
# Unzooms zoomed multi-pane windows so hidden panes are visible without
# changing which pane is active.

client="${1:-}"
display_args=()
switch_args=()
if [[ -n "$client" ]]; then
  display_args=(-t "$client")
  switch_args=(-c "$client")
fi

fmt='#{session_name}'$'\t''#{window_index}'$'\t'
fmt+='#{window_zoomed_flag}'$'\t''#{window_panes}'

IFS=$'\t' read -r cur_sess cur_win cur_zoom cur_panes < <(
  tmux display-message "${display_args[@]}" -p "$fmt"
)

# If starting on a zoomed multi-pane window, unzoom it first so its sibling
# panes are visible before advancing on the next press.
if [[ "$cur_zoom" == "1" && "${cur_panes:-1}" -gt 1 ]]; then
  tmux resize-pane -Z -t "=${cur_sess}:${cur_win}"
  exit 0
fi

sessions=()
windows=()
zooms=()
panes=()
cur_idx=-1
i=0

while IFS=$'\t' read -r s w z p; do
  sessions+=("$s")
  windows+=("$w")
  zooms+=("$z")
  panes+=("$p")
  if [[ "$s" == "$cur_sess" && "$w" == "$cur_win" ]]; then
    cur_idx=$i
  fi
  ((i += 1))
done < <(tmux list-windows -a -F "$fmt")

total=${#sessions[@]}
if (( total == 0 || (total <= 1 && cur_idx >= 0) )); then
  exit 0
fi

next_idx=$(( (cur_idx + 1) % total ))
next_sess="${sessions[$next_idx]}"
next_win="${windows[$next_idx]}"
next_zoom="${zooms[$next_idx]}"
next_panes="${panes[$next_idx]}"
target="=${next_sess}:${next_win}"

tmux select-window -t "$target"
if [[ "$next_zoom" == "1" && "${next_panes:-1}" -gt 1 ]]; then
  tmux resize-pane -Z -t "$target"
fi
if [[ "$next_sess" != "$cur_sess" ]]; then
  tmux switch-client "${switch_args[@]}" -t "=${next_sess}"
fi

#!/usr/bin/env bash
set -euo pipefail

script="$1"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

{
  printf '#!%s\n' "$BASH"
  cat <<'EOF'
set -euo pipefail
printf '%s\n' "$*" >>"$TMUX_LOG"
case "$1" in
  display-message)
    printf '%s\n' "$TMUX_CURRENT"
    ;;
  list-windows)
    printf '%s\n' "$TMUX_WINDOWS"
    ;;
  select-window|resize-pane|switch-client)
    ;;
  *)
    echo "unexpected tmux subcommand: $*" >&2
    exit 1
    ;;
esac
EOF
} >"$tmpdir/tmux"
chmod +x "$tmpdir/tmux"

export PATH="$tmpdir:$PATH"

assert_calls() {
  local name="$1"
  local expected="$2"
  local actual
  actual="$(cat "$TMUX_LOG")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n' \
      "$name" "$expected" "$actual" >&2
    exit 1
  fi
}

run_case() {
  local name="$1"
  local current="$2"
  local windows="$3"
  local client="$4"
  local expected="$5"

  TMUX_LOG="$tmpdir/log"
  : >"$TMUX_LOG"
  export TMUX_LOG TMUX_CURRENT="$current" TMUX_WINDOWS="$windows"

  if [[ -n "$client" ]]; then
    "$script" "$client"
  else
    "$script"
  fi
  assert_calls "$name" "$expected"
}

fmt=$'#{session_name}\t#{window_index}\t#{window_zoomed_flag}\t#{window_panes}'

# 1. Unzoom a zoomed multi-pane starting window in place.
run_case "unzoom-start" \
  $'alpha\t1\t1\t2' \
  $'alpha\t1\t1\t2\nalpha\t2\t0\t1' \
  "/dev/pts/1" \
  "display-message -t /dev/pts/1 -p $fmt
resize-pane -Z -t =alpha:1"

# 2. Advance to the next window in the same session.
run_case "next-in-session" \
  $'alpha\t1\t0\t2' \
  $'alpha\t1\t0\t2\nalpha\t2\t0\t1' \
  "" \
  "display-message -p $fmt
list-windows -a -F $fmt
select-window -t =alpha:2"

# 3. Advance to the next session and unzoom a zoomed multi-pane destination.
run_case "next-session-unzoom-dest" \
  $'alpha\t2\t0\t1' \
  $'alpha\t1\t0\t1\nalpha\t2\t0\t1\nbeta\t0\t1\t3' \
  "/dev/pts/2" \
  "display-message -t /dev/pts/2 -p $fmt
list-windows -a -F $fmt
select-window -t =beta:0
resize-pane -Z -t =beta:0
switch-client -c /dev/pts/2 -t =beta"

# 4. Wrap around from the last window of the last session to the first.
run_case "wrap-around" \
  $'beta\t0\t0\t1' \
  $'alpha\t1\t0\t1\nbeta\t0\t0\t1' \
  "" \
  "display-message -p $fmt
list-windows -a -F $fmt
select-window -t =alpha:1
switch-client -t =alpha"

# 5. Single window is a no-op.
run_case "single-window-noop" \
  $'alpha\t1\t0\t1' \
  $'alpha\t1\t0\t1' \
  "" \
  "display-message -p $fmt
list-windows -a -F $fmt"

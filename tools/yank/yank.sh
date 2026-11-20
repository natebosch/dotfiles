# Copy stdin, a file, or a string argument to the tmux paste buffer or
# system clipboard.
#
# Usage:
#   some-command | yank [-h|--host]
#   yank [-h|--host] path/to/file
#   yank [-h|--host] 'literal text'
#
# Inside tmux, writes only to the tmux paste buffer by default (`tmux
# load-buffer -`). Outside tmux, or when -h/--host is passed, copies to
# the host/OS clipboard using, in order: xsel or xclip when an X display
# is available, pbcopy (when not over SSH), and an OSC52 escape sequence
# written to the tmux client tty or /dev/tty.

# OSC52 payloads are limited by many terminals; keep the original limit.
max_len=74994

die() {
  echo "${0##*/}: $*" >&2
  exit 1
}

host=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--host)
      host=true
      shift
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      break
      ;;
  esac
done

if [ "$#" -gt 1 ]; then
  die "requires 0 or 1 arguments"
fi
if [ "$#" -eq 0 ]; then
  buf="$(head -c "$((max_len + 1))")"
elif [ -f "$1" ]; then
  buf="$(head -c "$((max_len + 1))" "$1")"
else
  buf="$1"
fi

len="$(printf %s "$buf" | wc -c)"
if [ "$len" -gt "$max_len" ]; then
  echo "${0##*/}: input too long, max size $max_len bytes" >&2
  exit 65
fi

has() {
  command -v "$1" >/dev/null 2>&1
}

in_tmux=false
if [ -n "${TMUX:-}${TMUX_PANE:-}" ] && has tmux; then
  in_tmux=true
fi

if "$in_tmux" && ! "$host"; then
  if tmux_err="$(printf %s "$buf" | tmux load-buffer - 2>&1)"; then
    exit 0
  fi
  die "tmux load-buffer failed: $tmux_err"
fi

if "$in_tmux"; then
  # Pick up the DISPLAY and SSH_CONNECTION of the most recently attached
  # client, which may differ from the ones this shell was started with.
  eval "$(tmux show-environment -s DISPLAY 2>/dev/null || true)"
  eval "$(tmux show-environment -s SSH_CONNECTION 2>/dev/null || true)"
fi

if [ -n "${DISPLAY:-}" ]; then
  if has xsel; then
    printf %s "$buf" | xsel -ib && exit 0
  elif has xclip; then
    printf %s "$buf" | xclip -sel c && exit 0
  fi
fi

if [ -z "${SSH_CONNECTION:-}" ] && has pbcopy; then
  printf %s "$buf" | pbcopy && exit 0
fi

tty_target=/dev/tty
term_name="${TERM:-}"
if "$in_tmux"; then
  client_tty="$(tmux display-message -p '#{client_tty}' 2>/dev/null || true)"
  if [ -n "$client_tty" ]; then
    tty_target="$client_tty"
    term_name="$(tmux display-message -p '#{client_termname}' 2>/dev/null || true)"
  fi
fi

if [ -z "$term_name" ] || [ "$term_name" = dumb ]; then
  die "no clipboard method available (TERM=${term_name:-unset} lacks OSC52)"
fi

encoded="$(printf %s "$buf" | base64 | tr -d '\r\n')"

{ printf '\033]52;c;%s\a' "$encoded" >"$tty_target"; } 2>/dev/null ||
  die "no clipboard method available and $tty_target is not writable"

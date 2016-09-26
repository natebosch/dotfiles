# Fancy git prompt
function git-status() {
  GIT_STATUS=""
  git status &>/dev/null || return

  local PROMPT_PREFIX="("
  local PROMPT_SUFFIX=")"
  local PROMPT_SEPARATOR="|"
  local PROMPT_BRANCH="%{$fg_bold[magenta]%}"
  local PROMPT_STAGED="%{$fg[red]%}"
  local PROMPT_UNSTAGED="%{$fg[cyan]%}"
  local PROMPT_CONFLICTS="%{$fg[red]%}!"
  local PROMPT_AHEAD="%{$fg[white]%}+"
  local PROMPT_BEHIND="%{$fg[white]%}-"
  local PROMPT_UNTRACKED="%{$fg[red]%}."
  local PROMPT_CLEAN="%{$fg_bold[green]%}."

  local UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2&>/dev/null)"

  local BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  local GIT_AHEAD="$(git rev-list --left-only --count $BRANCH...$UPSTREAM)"
  local GIT_BEHIND="$(git rev-list --right-only --count $BRANCH...$UPSTREAM)"
  local GIT_CONFLICTS="$(git ls-files --unmerged | cut -f2 | sort -u | wc -l \
    | sed 's/^ *//')"
  local GIT_STAGED="$(git diff --cached --numstat | wc -l | sed 's/^ *//')"
  local GIT_UNSTAGED="$(git diff --numstat | wc -l | sed 's/^ *//')"
  local GIT_UNTRACKED="$(git ls-files -o --exclude-standard | wc -l \
    | sed 's/^ *//')"

  GIT_STATUS="$PROMPT_PREFIX$PROMPT_BRANCH$BRANCH%{${reset_color}%}"
  GIT_STATUS="$GIT_STATUS$PROMPT_SEPARATOR"
  GIT_CLEAN=true
  if [ "$GIT_AHEAD" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_AHEAD$GIT_AHEAD%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if [ "$GIT_BEHIND" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_BEHIND$GIT_BEHIND%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if [ "$GIT_CONFLICTS" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_CONFLICTS$GIT_CONFLICTS%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if [ "$GIT_STAGED" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_STAGED$GIT_STAGED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if [ "$GIT_UNSTAGED" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_UNSTAGED$GIT_UNSTAGED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if [ "$GIT_UNTRACKED" -ne "0" ]; then
    GIT_STATUS="$GIT_STATUS$PROMPT_UNTRACKED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if $GIT_CLEAN; then
    GIT_STATUS="$GIT_STATUS$PROMPT_CLEAN"
  fi
  GIT_STATUS="$GIT_STATUS%{${reset_color}%}$PROMPT_SUFFIX"
}

autoload add-zsh-hook
add-zsh-hook precmd git-status

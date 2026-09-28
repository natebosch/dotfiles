# Fancy git prompt
function git-status() {
  local GIT_OUTPUT
  GIT_OUTPUT="$(git --no-optional-locks status --porcelain=v2 --branch \
    2>/dev/null)" || return

  local PROMPT_PREFIX="("
  local PROMPT_SUFFIX=")"
  local PROMPT_SEPARATOR="|"
  local PROMPT_BRANCH="%{$fg_bold[magenta]%}"
  local PROMPT_STAGED="%{$fg[red]%}"
  local PROMPT_UNSTAGED="%{$fg[cyan]%}"
  local PROMPT_CONFLICTS="%{$fg[red]%}!"
  local PROMPT_UNTRACKED="%{$fg[red]%}."
  local PROMPT_CLEAN="%{$fg_bold[green]%}."

  local BRANCH UPSTREAM LINE
  local -a AHEAD_BEHIND
  local -i GIT_AHEAD=0 GIT_BEHIND=0 GIT_CONFLICTS=0 GIT_STAGED=0
  local -i GIT_UNSTAGED=0 GIT_UNTRACKED=0
  for LINE in "${(@f)GIT_OUTPUT}"; do
    case "$LINE" in
      '# branch.head '*) BRANCH="${LINE#'# branch.head '}" ;;
      '# branch.upstream '*) UPSTREAM="${LINE#'# branch.upstream '}" ;;
      '# branch.ab '*)
        AHEAD_BEHIND=(${=LINE#'# branch.ab '})
        GIT_AHEAD=${AHEAD_BEHIND[1]#+}
        GIT_BEHIND=${AHEAD_BEHIND[2]#-}
        ;;
      # Ordinary and renamed entries: "1 XY ..." or "2 XY ...", where X is
      # the staged state, Y is the unstaged state, and "." is unmodified.
      [12]' '*)
        [[ "${LINE[3]}" != . ]] && ((++GIT_STAGED))
        [[ "${LINE[4]}" != . ]] && ((++GIT_UNSTAGED))
        ;;
      'u '*) ((++GIT_CONFLICTS)) ;;
      '? '*) ((++GIT_UNTRACKED)) ;;
    esac
  done
  [[ "$BRANCH" == '(detached)' ]] && BRANCH=HEAD

  # Bold ahead/behind counts when tracking a remote rather than a local branch.
  if [[ -n "$UPSTREAM" && "$(git config "branch.$BRANCH.remote")" != . ]]; then
    local PROMPT_AHEAD="%{$fg_bold[white]%}+"
    local PROMPT_BEHIND="%{$fg_bold[white]%}-"
  else
    local PROMPT_AHEAD="%{$fg[white]%}+"
    local PROMPT_BEHIND="%{$fg[white]%}-"
  fi

  local BRANCH_DISPLAY="$BRANCH"
  if [[ "$BRANCH" == "$PROJECT"--* ]]; then
    BRANCH_DISPLAY=${BRANCH[((${#PROJECT} + 2)),${#BRANCH}]}
  fi
  local GIT_STATUS="$PROMPT_PREFIX$PROMPT_BRANCH$BRANCH_DISPLAY"
  GIT_STATUS="$GIT_STATUS%{${reset_color}%}$PROMPT_SEPARATOR"
  local GIT_CLEAN=true
  if ((GIT_AHEAD)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_AHEAD$GIT_AHEAD%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if ((GIT_BEHIND)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_BEHIND$GIT_BEHIND%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if ((GIT_CONFLICTS)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_CONFLICTS$GIT_CONFLICTS%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if ((GIT_STAGED)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_STAGED$GIT_STAGED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if ((GIT_UNSTAGED)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_UNSTAGED$GIT_UNSTAGED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if ((GIT_UNTRACKED)); then
    GIT_STATUS="$GIT_STATUS$PROMPT_UNTRACKED%{${reset_color}%}"
    GIT_CLEAN=false
  fi
  if $GIT_CLEAN; then
    GIT_STATUS="$GIT_STATUS$PROMPT_CLEAN"
  fi
  echo "$GIT_STATUS%{${reset_color}%}$PROMPT_SUFFIX"
}

zstyle ':completion::complete:git-checkout:argument-rest:commit-tag-refs' command "echo"
zstyle ':completion::complete:git-checkout:argument-rest:blob-tag-refs' command "echo"

# Fancy mercurial prompt

function hg-status() {
  local HG_STATUS=""
  local _IFS=$IFS
  IFS=$'\n'
  local UNPARSED
  UNPARSED=($(hg status -ramdu 2&>/dev/null))
  if [[ "$?" -ne "0" ]]; then
    IFS=$_IFS
    return
  fi
  IFS=$_IFS


  local PROMPT_PREFIX="("
  local PROMPT_SUFFIX=")"
  local PROMPT_UNCOMMITTED="%{$fg[cyan]%}"
  local PROMPT_UNTRACKED="%{$fg[red]%}."
  local PROMPT_CLEAN="%{$fg_bold[green]%}."



  local HG_UNCOMMITTED=0
  local HG_UNTRACKED=0
  local LINE
  for LINE in ${UNPARSED[*]}; do
    if [[ "$LINE" == M* ]] || [[ "$LINE" == A* ]] || \
      [[ "$LINE" == R* ]] || [[ "$LINE" == !* ]]; then
      (( HG_UNCOMMITTED++ ))
    elif [[ "$LINE" == ?* ]]; then
      (( HG_UNTRACKED++ ))
    fi
  done

  HG_STATUS="$PROMPT_PREFIX%{${reset_color}%}"
  HG_CLEAN=true
  if [ "$HG_UNCOMMITTED" -ne "0" ]; then
    HG_STATUS="$HG_STATUS$PROMPT_UNCOMMITTED$HG_UNCOMMITTED%{${reset_color}%}"
    HG_CLEAN=false
  fi
  if [ "$HG_UNTRACKED" -ne "0" ]; then
    HG_STATUS="$HG_STATUS$PROMPT_UNTRACKED%{${reset_color}%}"
    HG_CLEAN=false
  fi
  if $HG_CLEAN; then
    HG_STATUS="$HG_STATUS$PROMPT_CLEAN"
  fi
  echo "$HG_STATUS%{${reset_color}%}$PROMPT_SUFFIX"
}

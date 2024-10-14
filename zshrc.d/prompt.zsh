local PATH_COLOR=214
local TIME_COLOR=33
autoload colors
colors
setopt prompt_subst
local success="%B%(?,%F{green}»,%F{red}⁉)%b%f"
local working_dir="%F{$PATH_COLOR}%B%(7~,.../,)%6~%b%f"
PROMPT='$working_dir $(git-status)
%b[%F{$TIME_COLOR%}%B%T%b]$success%f  '

RPROMPT=""

# Reset the prompt every 10 seconds
TMOUT=10
TRAPALRM() {
  if [[ "$WIDGET" =~ "comp" || "$WIDGET" =~ "fzf" ]]; then
    return 0
  fi
  zle reset-prompt
}

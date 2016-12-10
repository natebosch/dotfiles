local PROMPT_COLOR=cyan
local PATH_COLOR=172
local TIME_COLOR=blue
autoload colors
colors
setopt prompt_subst
local smiley="%(?,%F{green}☺%f,%F{red}☹%f)"
local working_dir="%F{$PATH_COLOR}%B%(7~,.../,)%6~%b%f"
PROMPT="\$working_dir \$GIT_STATUS
%b[%F{$TIME_COLOR%}%B%T%b]\$smiley%f  "

RPROMPT=""

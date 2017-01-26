local PATH_COLOR=172
local TIME_COLOR=33
autoload colors
colors
setopt prompt_subst
local success="%B%(?,%F{green}»,%F{red}⁉)%b%f"
local working_dir="%F{$PATH_COLOR}%B%(7~,.../,)%6~%b%f"
PROMPT="\$working_dir \$GIT_STATUS
%b[%F{$TIME_COLOR%}%B%T%b]\$success%f  "

RPROMPT=""

local PROMPT_COLOR=cyan
local PATH_COLOR=green
local TIME_COLOR=blue
autoload colors
colors
setopt prompt_subst
local smiley="%(?,%{$fg[green]%}☺%{$reset_color%},%{$fg[red]%}☹%{$reset_color%})"
local working_dir="%{${fg[$PATH_COLOR]}%}%B%(7~,.../,)%6~%b%{${fg[default]}%}"
PROMPT="\$working_dir \$GIT_STATUS
%b[%{${fg[$TIME_COLOR]}%}%B%T%b]\$smiley%{${fg[default]}%}  "

RPROMPT=""

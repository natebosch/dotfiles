# Prompt and colors
local PROMPT_COLOR=cyan
local PATH_COLOR=green
local TIME_COLOR=blue
autoload colors
colors
setopt prompt_subst
#PS1="%{${fg[$PROMPT_COLOR]}%}%B%m%b[%{${fg[$TIME_COLOR]}%}%B%T%b]%(?..%{${fg[$ALERT_COLOR]}%}!)%{${fg[default]}%}$(git symbolic-ref HEAD | cut -d'/' -f3) "   # a nice colored prompt
local smiley="%(?,%{$fg[green]%}☺%{$reset_color%},%{$fg[red]%}☹%{$reset_color%})"
local working_dir="%{${fg[$PATH_COLOR]}%}%B%(7~,.../,)%6~%b%{${fg[default]}%}"
#local old_prompt="%{${fg[$PROMPT_COLOR]}%}%B%m%b[%{${fg[$TIME_COLOR]}%}%B%T%b]\$GIT_STATUS\$smiley%{${fg[default]}%} "
PROMPT="\$working_dir \$GIT_STATUS
%b[%{${fg[$TIME_COLOR]}%}%B%T%b]\$smiley%{${fg[default]}%}  "

#RPROMPT="%{${fg[$PROMPT_COLOR]}%}%B%(7~,.../,)%6~%b%{${fg[default]}%}"
RPROMPT=""

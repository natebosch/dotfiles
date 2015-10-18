# Fancy git prompt
function git-status() {
    GIT_STATUS=""
    ZSH_THEME_GIT_PROMPT_PREFIX="("
    ZSH_THEME_GIT_PROMPT_SUFFIX=")"
    ZSH_THEME_GIT_PROMPT_SEPARATOR="|"
    ZSH_THEME_GIT_PROMPT_BRANCH="%{$fg_bold[magenta]%}"
    ZSH_THEME_GIT_PROMPT_STAGED="%{$fg[red]%}."
    ZSH_THEME_GIT_PROMPT_CONFLICTS="%{$fg[red]%}."
    ZSH_THEME_GIT_PROMPT_CHANGED="%{$fg[cyan]%}."
    ZSH_THEME_GIT_PROMPT_REMOTE=""
    ZSH_THEME_GIT_PROMPT_UNTRACKED="."
    ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg_bold[green]%}."

    local gitstatus="$ZDOTDIR/.bin/gitstatus.py"
    if [ -x $gitstatus ]; then
        _GIT_GIT_STATUS=`python ${gitstatus}`
        __CURRENT_GIT_GIT_STATUS=("${(@f)_GIT_GIT_STATUS}")
        GIT_BRANCH=$__CURRENT_GIT_GIT_STATUS[1]
        GIT_REMOTE=$__CURRENT_GIT_GIT_STATUS[2]
        GIT_STAGED=$__CURRENT_GIT_GIT_STATUS[3]
        GIT_CONFLICTS=$__CURRENT_GIT_GIT_STATUS[4]
        GIT_CHANGED=$__CURRENT_GIT_GIT_STATUS[5]
        GIT_UNTRACKED=$__CURRENT_GIT_GIT_STATUS[6]
        GIT_CLEAN=$__CURRENT_GIT_GIT_STATUS[7]
        if [ -n "$__CURRENT_GIT_GIT_STATUS" ]; then
        GIT_STATUS="($GIT_BRANCH"
        GIT_STATUS="$ZSH_THEME_GIT_PROMPT_PREFIX$ZSH_THEME_GIT_PROMPT_BRANCH$GIT_BRANCH%{${reset_color}%}"
        if [ -n "$GIT_REMOTE" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_REMOTE$GIT_REMOTE%{${reset_color}%}"
        fi
        GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_SEPARATOR"
        if [ "$GIT_STAGED" -ne "0" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_STAGED$GIT_STAGED%{${reset_color}%}"
        fi
        if [ "$GIT_CONFLICTS" -ne "0" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_CONFLICTS$GIT_CONFLICTS%{${reset_color}%}"
        fi
        if [ "$GIT_CHANGED" -ne "0" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_CHANGED$GIT_CHANGED%{${reset_color}%}"
        fi
        if [ "$GIT_UNTRACKED" -ne "0" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_UNTRACKED%{${reset_color}%}"
        fi
        if [ "$GIT_CLEAN" -eq "1" ]; then
            GIT_STATUS="$GIT_STATUS$ZSH_THEME_GIT_PROMPT_CLEAN"
        fi
            GIT_STATUS="$GIT_STATUS%{${reset_color}%}$ZSH_THEME_GIT_PROMPT_SUFFIX"
        fi
        unset __CURRENT_GIT_GIT_STATUS
    fi
}

autoload add-zsh-hook
add-zsh-hook precmd git-status

setopt AUTO_PUSHD   # push directories on every cd
DIRSTACKSIZE=20     # number of directories in pushd/popd stack

alias ..='cd ..'
alias ...='cd ../..'
alias ....='cd ../../..'
alias .....='cd ../../../..'

alias b='popd'
alias d='dirs -v'

upto ()
{
    if [ -z "$1" ]; then
        return
    fi
    local upto=$1
    cd "${PWD/\/$upto\/*//$upto}"
}

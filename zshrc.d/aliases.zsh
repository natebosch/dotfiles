alias psg='ps aux | grep'
alias envg='env | grep'
alias ls='ls -F --color=auto'
alias lr='ls -lth --color | head'
alias tl='tmux ls'
alias sz="source $DOTDIR/.zshrc"
alias utc='date -u'
alias size="stty -a | tr ';' '\n' | egrep 'rows|columns'"
alias bell="print -n '\a'"
alias shuf="awk 'BEGIN{srand();}{print rand()\"\t\"\$1}' | sort -k1 -n | cut -f2-"
alias trim="sed 's/^ *//;s/ *$//'"
alias link_github="sed 's/\(https:\/\/github.com\/\([^ \/]*\/\)\{3\}\([[:digit:]]\+\)\)/[#\3](\1)/g'"
alias ag="ag -W100"

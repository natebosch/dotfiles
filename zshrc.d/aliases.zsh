alias psg='ps aux | grep'
alias envg='env | grep'
alias ls='ls -F --color=auto'
alias lr='ls -lth --color | head'
alias sz="source $HOME/.zshrc"
alias size="stty -a | tr ';' '\n' | egrep 'rows|columns'"
alias bell="print -n '\a'"
alias shuf="awk 'BEGIN{srand();}{print rand()\"\t\"\$1}' | sort -k1 -n | cut -f2-"
alias trim="sed 's/^ *//;s/ *$//'"
alias ag="ag -W100"
alias vt="vim +'term ++curwin'"

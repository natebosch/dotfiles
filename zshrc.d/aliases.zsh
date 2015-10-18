#useful aliases
alias open='gnome-open'
alias psg='ps aux | grep'
alias envg='env | grep'
alias ls='ls -F --color=auto'
alias la='ls -a'
alias ll='ls -lh'
alias lr='ls -lth --color | head'
alias lz='ls *[^z]' # ignore piled up .gz files
alias lt='ls -lht'
alias tl='tmux ls'
alias sz="source $DOTDIR/.zshrc"
alias utc='date -u'
alias synergy='synergys -c .synergy.conf -f'
alias size="stty -a | tr ';' '\n' | egrep 'rows|columns'"
alias schance="ssh chance -t '~/.bin/s 0'"
alias apg="apg -MS -m 8 -x 12 -s"
alias beep="echo "
alias bell="echo "
alias shuf="awk 'BEGIN{srand();}{print rand()\"\t\"\$0}' | sort -k1 -n | cut -f2-"
alias trim="sed 's/^ *//;s/ *$//'"

alias n='notes'
alias h='hourly'

if [ -f $DOTDIR/.vimrc ]; then
   if [ -x /usr/bin/vim ]; then
      alias vi="vim -u $DOTDIR/.vimrc"
   fi
fi

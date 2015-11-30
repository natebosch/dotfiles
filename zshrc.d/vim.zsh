# Ensure that a backup directory exists for vim
mkdir -p $DOTDIR/.vim/backup
mkdir -p $DOTDIR/.vim/tmp

if [ -f $DOTDIR/.vimrc ]; then
   if [ -x /usr/bin/vim ]; then
      alias vi="vim -u $DOTDIR/.vimrc"
   fi
fi

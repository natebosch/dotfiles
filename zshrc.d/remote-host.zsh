case $DOTDIR in
    *tmp*)
    REMOTE_HOST=1
    ;;
    *)
    return
    ;;
esac
if [ (-f $DOTDIR/.vimrc) && (-x /usr/bin/vim) ]; then 
    alias vi="/usr/bin/vim -u $DOTDIR/.vimrc -c \"set viminfo=\\\"\\\"\""
fi


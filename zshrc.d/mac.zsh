if [[ "$(uname)" != "Darwin" ]]; then
  return 0
fi
# The `ls` on mac is missing `--color=auto`
alias ls='ls -F -G'

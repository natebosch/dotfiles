if [[ "$(uname)" != "Darwin" ]]; then
  return 0
fi
# Brew should be early in the path
path=(
  $DOTDIR/.brew/bin
  $DOTDIR/.brew/opt/gnu-sed/libexec/gnubin
  $path
)
manpath=($DOTDIR/.brew/share/man(N-/) $manpath)
ld_library_path=($DOTDIR/.brew/lib(N-/) $ld_library_path)

export HOMEBREW_NO_GITHUB_API=true

# The `ls` on mac is missing `--color=auto`
alias ls='ls -F -G'

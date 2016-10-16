if [[ "$(uname)" == "Darwin" ]]; then
  # Brew should be early in the path
  path=($DOTDIR/.brew/bin(N-/) $path)
  manpath=($DOTDIR/.brew/share/man(N-/) $manpath)
  ld_library_path=($DOTDIR/.brew/lib(N-/) $ld_library_path)
  fpath+=($DOTDIR/.brew/completions/zsh)

  # The `ls` on mac is missing `--color=auto`
  alias ls='ls -F -G'
fi

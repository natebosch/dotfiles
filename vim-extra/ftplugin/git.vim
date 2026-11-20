setlocal tw=72

setlocal nolist

setlocal foldmethod=syntax

if &l:scrolloff < 2
  setlocal scrolloff=2
endif

call git_sticky#enable()

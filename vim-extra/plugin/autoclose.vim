augroup Autoclose
  autocmd!
  autocmd QuitPre * nested call s:AutocloseUtil()
augroup END

function! s:AutocloseUtil() abort
  if s:IsUtil() | return | endif
  " Always close window local location list
  silent! lclose
  " If this was the last non-util window, close them first.
  let l:real_win_count = 0
  let l:current_win = winnr()
  keepjumps keepalt windo let l:real_win_count += s:IsUtil() ? 0 : 1
  keepjumps keepalt execute string(l:current_win).' wincmd w'
  if l:real_win_count > 1 | return | endif
  windo if s:IsUtil() | quit! | endif
endfunction

" Whether this is a quickfix or preview window.
function! s:IsUtil() abort
  return &buftype == 'quickfix' || &pvw
endfunction

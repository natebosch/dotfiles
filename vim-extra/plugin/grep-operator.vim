nnoremap <silent> <leader>G :set operatorfunc=<SID>GrepOperator<cr>g@
vnoremap <silent> <leader>G :<c-u>call <SID>GrepOperator(visualmode())<cr>

function! s:GrepOperator(type)
  let reg = @@
  if a:type ==# 'v'
    execute 'normal! `<v`>y'
  elseif a:type ==# 'char'
    execute 'normal! `[v`]y'
  else
    return
  endif
  silent execute 'grep! -R '.shellescape(escape(@@,'#%.^$*+?()[{\\|')).' .'
  copen
  redraw!
  let @@ = reg
endfunction

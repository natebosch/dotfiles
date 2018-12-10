nnoremap <silent> gp :set operatorfunc=<SID>ReplaceOperator<cr>g@
vnoremap <silent> gp :<c-u>call <SID>ReplaceOperator(visualmode())<cr>

function! s:ReplaceOperator(type)
  if a:type ==# 'v'
    execute "normal! `<v`>\"_c\<c-r>0\<esc>"
  elseif a:type ==# 'char'
    execute "normal! `[v`]\"_c\<c-r>0\<esc>"
  endif
endfunction

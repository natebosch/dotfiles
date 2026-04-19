nnoremap <silent> <leader>y :set operatorfunc=<SID>Operator<cr>g@
vnoremap <silent> <leader>y :<c-u>call <SID>Operator('', v:true)<cr>

function! s:Operator(type, ...) abort
  let l:reg = @@
  let l:is_visual = get(a:, 0, v:false)
  if l:is_visual
    execute 'normal! gvy'
  elseif a:type ==# 'char'
    execute 'normal! `[v`]y'
  elseif a:type ==# 'line'
    execute 'normal! ''[v'']y'
  else
    return
  endif
  call system('yank', @@)
  let @@ = l:reg
endfunction

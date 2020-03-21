setlocal makeprg=shellcheck\ -f\ gcc\ %
map <buffer> <leader>fl :make \| cwindow<cr><cr>

nnoremap <buffer> <leader>ff :call <SID>Format()<cr>

function! s:Format() abort
  let buffer_content = getline(1, '$')
  let lines = systemlist('shfmt -i 2 -ci', join(buffer_content, "\n"))
  if 0 == v:shell_error
    let win_view = winsaveview()
    silent keepjumps call setline(1, lines)
    if line('$') > len(lines)
      silent keepjumps execute string(len(lines)+1).',$ delete'
    endif
    call winrestview(win_view)
  else
    echom 'shfmt failed!'
    for line in lines
      echom line
    endfor
  endif
endfunction

setlocal formatoptions-=t

let b:project_nav_root_markers = ['pyproject.toml', 'setup.py']

nnoremap <buffer> <leader>ff :call <SID>Format()<cr>

function! s:Format() abort
  let l:filename = expand('%:p')
  if empty(l:filename)
    let l:filename = 'stdin.py'
  endif
  if executable('ruff')
    let l:cmd = 'ruff format --stdin-filename '.shellescape(l:filename).' -'
    let l:tool = 'ruff'
  elseif executable('black')
    let l:cmd = 'black --quiet --stdin-filename '.shellescape(l:filename).' -'
    let l:tool = 'black'
  else
    echom 'No python formatter found (ruff or black)'
    return
  endif
  let l:lines = systemlist(l:cmd, getline(1, '$'))
  if 0 == v:shell_error
    if empty(l:lines)
      let l:lines = ['']
    endif
    let l:win_view = winsaveview()
    silent keepjumps call setline(1, l:lines)
    if line('$') > len(l:lines)
      silent keepjumps execute string(len(l:lines)+1).',$ delete _'
    endif
    call winrestview(l:win_view)
  else
    echom l:tool.' format failed!'
    for l:line in l:lines
      echom l:line
    endfor
  endif
endfunction

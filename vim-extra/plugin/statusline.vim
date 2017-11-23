augroup Status
  autocmd!
  autocmd VimEnter,WinEnter,BufWinEnter * let w:is_active = v:true
  autocmd WinLeave * let w:is_active = v:false
augroup END

function! s:IfStatusMode(modes) abort
  if !exists('w:is_active') || !w:is_active | return '' | endif
  if &filetype == 'help' | return '' | endif
  if has_key(a:modes, mode())
    return '  '.a:modes[mode()].(&paste ? '[p]' : '').' '
  endif
  return ''
endfunction

function! StatusNormal() abort
  return s:IfStatusMode({'n': 'Normal', 'no': 'Normal'})
endfunction

function! StatusSpecial() abort
  if !exists('w:is_active') || !w:is_active | return '' | endif
  if &filetype == 'help' | return '  help ' | endif
  return ''
endfunction

function! StatusVisual() abort
  return s:IfStatusMode({'v': 'Visual', 'V': 'Visual line',
      \ "\<c-v>": 'Visual block'})
endfunction

function! StatusInsert() abort
  return s:IfStatusMode({'i': 'Insert',
      \'R': 'Replace', 'Rv': 'Replace virtual'})
endfunction

function! StatusSelect() abort
  return s:IfStatusMode({'s': 'Select', 'S': 'Select line',
      \ "\<c-s>": 'Select block'})
endfunction

autocmd CursorHold,BufWritePost * unlet! b:status_trailing_space

function! StatusTrailingSpaceWarning() abort
  if &readonly | return '' | endif
  if !exists("b:status_trailing_space")
    let bad_line = search('\s$', 'nw')
    let b:status_trailing_space = bad_line != 0 ? '\s:'.bad_line : ''
  endif
  return b:status_trailing_space
endfunction

function! GitBranch() abort
  if !exists('*fugitive#head') | return '' | endif
  let branch = fugitive#head()
  if branch != ''
    return '['.branch.']'
  endif
  return ''
endfunction

function! LSCInfo() abort
  if !has_key(g:lsc_server_commands, &filetype) | return '' | endif
  let l:status = LSCServerStatus()
  let l:version = lsc#file#version()
  if version == '' | return '' | endif
  return '[LSC:'.l:status.':'.l:version.']'
endfunction

set laststatus=2
set statusline=
" Color and mode only in active window
set statusline+=%#StatusNormal#%{StatusNormal()}%{StatusSpecial()}
set statusline+=%#StatusVisual#%{StatusVisual()}
set statusline+=%#StatusInsert#%{StatusInsert()}
set statusline+=%#StatusSelect#%{StatusSelect()}
" Reset highlight
set statusline+=%0*
"Truncate from here
set statusline+=%\<
" File, modified
set statusline+=\ %f%m
" Right align the rest
set statusline+=%=
" Trailing whitespace warning
set statusline+=%#WarningMsg#%{StatusTrailingSpaceWarning()}%0*
" Filetype
set statusline+=%y
set statusline+=%{GitBranch()}
" LSC file version
set statusline+=%{LSCInfo()}
" Lines/Total|Column percent
set statusline+=%2c\|%l/%L\ %3p%%

set noshowmode
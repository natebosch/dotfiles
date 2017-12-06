nnoremap <leader>cd :call <SID>LcdProjectRoot(expand('%:p'))<cr>
nnoremap <leader>bt :call <SID>BreakTab()<cr>

let g:project_nav_root_markers = ['.git/', 'BUILD']

function! s:LcdProjectRoot(from) abort
  let root = s:FindPackageRoot(a:from)
  if type(root) == v:t_string
    exec 'lcd' root
  else
    echoerr 'Could not file: '.root
  endif
endfunction

function! s:FindPackageRoot(from) abort
  for path in s:ParentDirectories(a:from)
    if exists('b:project_nav_root_markers')
      if s:ContainsAny(path, b:project_nav_root_markers) | return path | endif
    endif
    if s:ContainsAny(path, g:project_nav_root_markers) | return path | endif
  endfor
endfunction

" Wheter `path` contains any children from `markers`.
function! s:ContainsAny(path, markers) abort
  for marker in a:markers
    if marker[-1:] ==# '/'
      if isdirectory(a:path.'/'.marker) | return v:true | endif
    else
      if filereadable(a:path.'/'.marker) | return v:true | endif
    endif
  endfor
  return v:false
endfunction

" Returns a list of the parents of the current file up to a root directory.
function! s:ParentDirectories(from) abort
  let dirs = []
  let current_dir = a:from
  let parent = fnamemodify(current_dir, ':h')
  while parent != current_dir
    call add(dirs, current_dir)
    let current_dir = parent
    let parent = fnamemodify(parent, ':h')
  endwhile
  call add(dirs, current_dir)
  return dirs
endfunction

function! s:BreakTab() abort
  let previous = expand('#')
  let current = expand('%')
  if previous ==# '' && winnr('$') > 1
    close
  else
    b#
  endif
  exec 'tabedit' current
  call s:LcdProjectRoot(current)
endfunction

nnoremap <leader>cd :call <SID>LcdProjectRoot(expand('%:p'))<cr>
nnoremap <leader>gt :execute 'tabn '.g:lasttab<cr>
nnoremap <leader>n :call <SID>OpenNotes()<cr>
nnoremap <leader>lf :call <SID>ListFiles(expand('%:p'))<cr>
nnoremap <leader>lF :call fzf#vim#files(getcwd(-1))<cr>

set tabline=%!ProjectTabLine()

let g:project_nav_root_markers = ['.git', '.git/', 'BUILD']

augroup ProjectNav
  autocmd!
  autocmd BufNewFile,BufRead notes.md
      \ if !exists('t:tab_name') &&
      \   len(gettabinfo(tabpagenr())[0].windows) == 1
      \ |   let t:tab_name = 'notes'
      \ | endif
  autocmd TabLeave * let g:lasttab = tabpagenr()
augroup END

function! s:ListFiles(from) abort
  let l:root = s:FindPackageRoot(a:from)
  if type(l:root) == v:t_string
    call fzf#vim#files(l:root)
  else
    Files
  endif
endfunction

function! s:LcdProjectRoot(from) abort
  let root = s:FindPackageRoot(a:from)
  if type(root) == v:t_string
    exec 'windo lcd' root
    let t:tab_name = fnamemodify(root, ':t')
  else
    echoerr 'Could not find: '.root
  endif
endfunction

function! s:TryLcdProjectRoot() abort
  try
    let root = s:FindPackageRoot(expand('%:p'))
    if type(root) == v:t_string
      exec 'windo lcd' root
      let t:tab_name = fnamemodify(root, ':t')
    endif
  endtry
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
  let current = expand('%:p')
  if previous ==# '' && winnr('$') > 1
    close
  else
    b#
  endif
  exec 'tabedit' current
  call s:LcdProjectRoot(current)
endfunction

function! ProjectTabLine()
  let line = ''
  let single_name = s:SingleTabName()
  let use_tab_names = 1
  if single_name !=# ''
    let line .= single_name.'|'
    let use_tab_names = 0
  endif
  for i in range(tabpagenr('$'))
    if i + 1 == tabpagenr()
      let line .= '%#TabLineSel#'
    else
      let  line .= '%#TabLine#'
    endif
    let line .= '%'.(i + 1).'T'
    let line .= string(i + 1)
    let line .= ' %{ProjectTabLabel('.(i+1).','.use_tab_names.')} '
  endfor
  let  line .= '%#TabLine#'
  let line .= '%#TabLineFill%T'
  return line
endfunction

function! ProjectTabLabel(n, use_tab_names) abort
  let buflist = tabpagebuflist(a:n)
  let winnr = tabpagewinnr(a:n)

  let tab_name = ''
  if a:use_tab_names
    let tab_name = gettabvar(a:n, 'tab_name')
  endif
  if tab_name ==# ''
    let file_path = fnamemodify(bufname(buflist[winnr - 1]), ':~:.')
    let tab_name .= StatusPathShorten(file_path)
  elseif s:HasMultipleTabs(tab_name)
    let file_path = fnamemodify(bufname(buflist[winnr - 1]), ':t')
    let tab_name .= '|'.file_path
  endif

  let modified = v:false
  for bufnr in uniq(copy(buflist))
    if getbufinfo(bufnr)[0].changed
        \ && getbufvar(bufnr ,'&buftype') !=# 'terminal'
      let modified = v:true
      break
    endif
  endfor

  let label = tab_name
  if modified | let label .= '[+]' | endif
  return label
endfunction

" If every tab has the same `tab_name` variable return it, else empty string.
function! s:SingleTabName() abort
  let seen_name = ''
  for i in range(tabpagenr('$'))
    let tab_name = gettabvar(i+1, 'tab_name')
    if tab_name ==# '' | return '' | endif
    if seen_name !=# '' && seen_name !=# tab_name | return '' | endif
    let seen_name = tab_name
  endfor
  return seen_name
endfunction

" Returns true if multiple tabs are named `tab_name`
function! s:HasMultipleTabs(tab_name) abort
  let seen = v:false
  for i in range(tabpagenr('$'))
    let tab_name = gettabvar(i+1, 'tab_name')
    if tab_name ==# '' | continue | endif
    if tab_name == a:tab_name
      if seen | return v:true | endif
      let seen = v:true
    endif
  endfor
  return v:false
endfunction

function! s:OpenNotes() abort
  if tabpagenr() == 1 && gettabvar(1, 'tab_name') ==# 'notes'
    if exists('g:lasttab')
      execute 'tabn '.g:lasttab
      return
    endif
  endif
  if gettabvar(1, 'tab_name') ==# 'notes'
    tabfirst
    return
  endif
  if empty($PROJECT)
    call s:OpenScratchBuffer()
    return
  endif
  tabedit $HOME/projects/$PROJECT/notes.md
  tabmove 0
  let g:lasttab = get(g:, 'lasttab', 0) + 1
  let t:tab_name = 'notes'
endfunction

function! s:OpenScratchBuffer()
  let scr_bufnr = bufnr('__scratch__')
  if scr_bufnr == -1
    enew
    setlocal filetype=markdown
    setlocal bufhidden=hide
    setlocal nobuflisted
    setlocal buftype=nofile
    setlocal noswapfile
    file __scratch__
  else
    execute 'buffer ' . scr_bufnr
  endif
endfunction

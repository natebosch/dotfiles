nnoremap <silent> <leader>g :set operatorfunc=<SID>OperatorLocalCwd<cr>g@
vnoremap <silent> <leader>g :<c-u>call <SID>Operator(visualmode(), v:false)<cr>
nnoremap <silent> <leader>G :set operatorfunc=<SID>OperatorGlobalCwd<cr>g@
vnoremap <silent> <leader>G :<c-u>call <SID>Operator(visualmode(), v:true)<cr>
nnoremap <silent> <leader>s :call <SID>Search('', v:false)<cr>
nnoremap <silent> <leader>S :call <SID>Search('', v:true)<cr>

function! s:OperatorLocalCwd(type)
  call s:Operator(a:type, v:false)
endfunction

function! s:OperatorGlobalCwd(type)
  call s:Operator(a:type, v:true)
endfunction

function! s:Operator(type, globalcwd)
  let l:reg = @@
  if a:type ==# 'v'
    execute 'normal! `<v`>y'
  elseif a:type ==# 'char'
    execute 'normal! `[v`]y'
  else
    return
  endif
  let l:search = escape(@@,'#%.^$*+?()[{\\|')
  let @@ = l:reg
  call s:Search(l:search, a:globalcwd)
endfunction

function! s:Search(query, globalcwd)
  let command_fmt = 'ag --nogroup --color --ignore .git/ %s || true'
  let initial_command =
      \ empty(a:query) ? 'true' : printf(command_fmt, shellescape(a:query))
  let reload_command = printf(command_fmt, '{q}')
  let spec = {
      \ 'options': [
      \   '--phony', '--query', a:query,
      \   '--prompt', 'Search: ', '--reverse',
      \   '--bind', 'change:reload:'.reload_command,
      \   '--bind', 'ctrl-a:select-all',
      \ ],
      \}
  if a:globalcwd
    let l:spec.dir = getcwd(-1)
  endif
  call fzf#vim#grep(initial_command, 1, fzf#vim#with_preview(spec), v:false)
endfunction

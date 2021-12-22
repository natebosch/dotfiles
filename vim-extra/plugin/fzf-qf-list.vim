command! QuickFix call <SID>QuickFix()
command! LocationList call <SID>LocationList()
command! KillBuffers call <SID>KillBuffers()

function! s:QuickFix() abort
  call s:FuzzyPick(getqflist(), 'cc')
endfunction

function! s:LocationList() abort
  call s:FuzzyPick(getloclist(0), 'll')
endfunction

function! s:FuzzyPick(items, jump) abort
  let items = map(a:items, {idx, item ->
      \ bufname(item.bufnr).':'.string(item.lnum).': '.item.text.' '.string(idx)})
  let l:options = fzf#wrap(
      \ fzf#vim#with_preview({
      \   'source': items,
      \   'sink': function('<SID>Pick', [a:jump]),
      \   'options': ['--with-nth', '..-2'],
      \ })
      \)
  call fzf#run(l:options)
endfunction

function! s:Pick(jump, item) abort
  let idx = split(a:item, ' ')[-1]
  execute a:jump idx + 1
endfunction

cnoremap <c-t> <c-r>=<SID>FzfRead()<cr>

" Run fzf and return the chosen file path rather than edit it.
function! s:FzfRead() abort
  let l:choice = ''
  function! s:Choose(choice) closure abort
    let l:choice = a:choice
  endfunction
  call fzf#run(fzf#wrap(fzf#vim#with_preview({
      \ 'sink': funcref('<SID>Choose'),
      \})))
  delfunction s:Choose
  return l:choice
endfunction

function! s:KillBuffers() abort
  call fzf#run(fzf#wrap({
      \ 'source': s:BufferList(),
      \ 'sink*': { lines -> s:Wipeout(lines) },
      \ 'options': '--multi --reverse'
      \ }))
endfunction

function! s:BufferList()
  redir => list
  silent ls
  redir END
  return split(list, "\n")
endfunction

function! s:Wipeout(lines)
  execute 'bwipeout' join(map(a:lines, {_, line -> split(line)[0]}))
endfunction


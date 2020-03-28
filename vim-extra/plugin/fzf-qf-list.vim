command! QuickFix call <SID>QuickFix()
command! LocationList call <SID>LocationList()

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

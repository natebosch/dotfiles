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
      \ string(idx).' '.bufname(item.bufnr).' '.item.text})
  call fzf#run({'source': items, 'sink': function('<SID>Pick', [a:jump]),
      \'options': '--with-nth 2.. --reverse', 'down': '40%'})
endfunction

function! s:Pick(jump, item) abort
  let idx = split(a:item, ' ')[0]
  execute a:jump idx + 1
endfunction

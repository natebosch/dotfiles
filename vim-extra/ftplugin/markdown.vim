let b:delimitMate_nesting_quotes = ['`']

let g:markdown_fenced_languages = [
    \ 'dart',
    \ 'html',
    \ 'sh',
    \ 'zsh=sh',
    \ ]

if &modifiable && !&previewwindow
  setlocal spell
endif
setlocal autoindent

nnoremap <leader>dr :call <SID>LinkifyGithub()<cr>

function! s:LinkifyGithub() abort
  let l:line = getline('.')
  let l:commits_replaced = substitute(l:line,
      \ '\%(\s\|^\)\zs\(https:\/\/github.com\/\([^ \/]*\/\)\{2\}commit\/\([[:xdigit:]]\{7\}\)[[:xdigit:]]\+\)',
      \ '[\3](\1)',
      \ ''
      \)
  let l:issues_replaced = substitute(l:commits_replaced,
      \ '\%(\s\|^\)\zs\(https:\/\/github.com\/\([^ \/]*\/\)\{3\}\([[:digit:]]\+\)\)', '[#\3](\1)', '')
  if l:issues_replaced !=# l:line
    call setline('.', l:issues_replaced)
  endif
endfunction

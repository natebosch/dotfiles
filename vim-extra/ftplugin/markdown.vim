let b:delimitMate_nesting_quotes = ["`"]

let g:markdown_fenced_languages = [
    \ 'dart',
    \ 'html',
    \ 'sh',
    \ 'zsh=sh',
    \ ]

setlocal spell
setlocal autoindent

command! -buffer -nargs=0 LinkifyGithub :call <SID>LinkifyGithub()

function! s:LinkifyGithub() abort
  " Commits
  %s/\%(\s\|^\)\zs\(https:\/\/github.com\/\([^ \/]*\/\)\{2\}commit\/\([[:xdigit:]]\{7\}\)[[:xdigit:]]\+\)/[\3](\1)/e
  " Issues and Pull Requests
  %s/\%(\s\|^\)\zs\(https:\/\/github.com\/\([^ \/]*\/\)\{3\}\([[:digit:]]\+\)\)/[#\3](\1)/e
endfunction

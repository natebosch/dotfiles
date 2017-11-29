let b:delimitMate_nesting_quotes = ["`"]

let g:markdown_fenced_languages = [
    \ 'dart',
    \ 'html',
    \ 'sh',
    \ 'zsh=sh',
    \ ]

setlocal spell

command! -buffer -nargs=0 LinkifyGithub :%s/\%(\s\|^\)\zs\(https:\/\/github.com\/\([^ \/]*\/\)\{3\}\([[:digit:]]\+\)\)/[#\3](\1)/

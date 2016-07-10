let b:delimitMate_nesting_quotes = ["'"]

map <buffer> <leader>ff :w<cr>:! dartfmt -w %<cr>:edit!<cr>

setlocal textwidth=80
setlocal formatoptions-=t

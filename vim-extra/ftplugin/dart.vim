let b:delimitMate_nesting_quotes = ["'"]

map <buffer> <leader>ff :w<cr>:! dartfmt -w %<cr>:redraw!<cr>

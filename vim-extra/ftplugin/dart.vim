let b:delimitMate_nesting_quotes = ["'"]

map <buffer> <leader>ff :DartFmt<cr>
vmap <buffer> <leader>fc :! dartfmt-comment<cr>
vmap <buffer> <leader>fs :! dartfmt-comment --implied-method<cr>

setlocal formatoptions-=t

noremap <buffer> <leader>tm 0/=><cr>dwi{<cr>return <esc>A<cr>}<esc>:noh<cr>
noremap <buffer> <leader>tr 0/return<cr>wd$kJJ%"_c$=> <esc>p:noh<cr>

let dart_html_in_strings=v:true

let b:delimitMate_nesting_quotes = ["'", '"', '`']

map <buffer> <leader>ff :DartFmt<cr>
vmap <buffer> <leader>fc :! dartfmt-comment<cr>
vmap <buffer> <leader>fs :! dartfmt-comment --implied-method<cr>

setlocal formatoptions-=t

noremap <buffer> <leader>tr :DartToggleMethodBodyType<cr>

let b:project_nav_root_markers = ['pubspec.yaml']

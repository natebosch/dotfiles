let b:delimitMate_nesting_quotes = ["'", '"', "`"]

map <buffer> <leader>ff :DartFmt<cr>
vmap <buffer> <leader>fc :! dartfmt-comment<cr>
vmap <buffer> <leader>fs :! dartfmt-comment --implied-method<cr>

setlocal formatoptions-=t

noremap <buffer> <leader>tm 0/=><cr>dwi{<cr>return <esc>A<cr>}<esc>:noh<cr>
noremap <buffer> <leader>tr
    \ :LSClientFindCodeActions '\v\cConvert to (expression<bar>block) body'<cr>

command! -buffer -nargs=0 DartGetAnalysisServerPort call <SID>GetServerPort()

let b:project_nav_root_markers = ['pubspec.yaml']

function! s:GetServerPort() abort
  call lsc#server#userCall('dart/diagnosticServer', v:null,
      \ function("<SID>PrintServerPort"))
endfunction

function! s:PrintServerPort(result) abort
  echom 'Analysis Server Diagnostic Port: '.string(a:result)
endfunction

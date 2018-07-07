let b:delimitMate_nesting_quotes = ["'", '"', "`"]

map <buffer> <leader>ff :DartFmt<cr>
vmap <buffer> <leader>fc :! dartfmt-comment<cr>
vmap <buffer> <leader>fs :! dartfmt-comment --implied-method<cr>

setlocal formatoptions-=t

noremap <buffer> <leader>tm 0/=><cr>dwi{<cr>return <esc>A<cr>}<esc>:noh<cr>
noremap <buffer> <leader>tr
    \ :LSClientFindCodeActions '\v\cConvert to (expression<bar>block) body'<cr>

let dart_html_in_strings=v:true

command! -buffer -nargs=0 DartNoHtmlInStrings call <SID>NoHtmlInStrings()
command! -buffer -nargs=0 DartGetAnalysisServerPort call <SID>GetServerPort()

function! s:NoHtmlInStrings() abort
  syntax cluster dartRawStringContains remove=@HTML
endfunction

let b:project_nav_root_markers = ['pubspec.yaml']

function! s:GetServerPort() abort
  call lsc#server#userCall('dart/getServerPort', v:null,
      \ function("<SID>PrintServerPort"))
endfunction

function! s:PrintServerPort(result) abort
  echom 'Analysis Server Diagnostic Port: '.string(a:result)
endfunction

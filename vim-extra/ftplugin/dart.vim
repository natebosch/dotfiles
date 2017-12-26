let b:delimitMate_nesting_quotes = ["'", '"', "`"]

map <buffer> <leader>ff :DartFmt<cr>
vmap <buffer> <leader>fc :! dartfmt-comment<cr>
vmap <buffer> <leader>fs :! dartfmt-comment --implied-method<cr>

setlocal formatoptions-=t

noremap <buffer> <leader>tm 0/=><cr>dwi{<cr>return <esc>A<cr>}<esc>:noh<cr>
noremap <buffer> <leader>tr :call ToggleDartBodyType()<cr>

let dart_html_in_strings=v:true

command! -buffer -nargs=0 DartNoHtmlInStrings call <SID>NoHtmlInStrings()

function! s:NoHtmlInStrings() abort
  syntax cluster dartRawStringContains remove=@HTML
endfunction

let b:project_nav_root_markers = ['pubspec.yaml']

function! ToggleDartBodyType() abort
  call lsc#edit#findCodeActions(function("<SID>ConvertBodyType"))
endfunction

function! s:ConvertBodyType(actions) abort
  for action in a:actions
    if action.title =~? 'Convert into block body' ||
        \ action.title =~? 'Convert into expression body'
      return action
    endif
  endfor
  return v:false
endfunction

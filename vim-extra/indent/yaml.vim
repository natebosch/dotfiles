if exists('b:did_custom_indent')
  finish
endif
let b:did_custom_indent = 1

setlocal indentexpr=YamlIndent()

let b:undo_indent = 'setl cin< cino<'

if exists('*YamlIndent')
  finish
endif

function! YamlIndent()
  let l:indentTo = GetYAMLIndent(v:lnum)

  let l:currentLine = trim(getline(v:lnum))
  let l:previousLine = trim(getline(prevnonblank(v:lnum - 1)))

  if l:currentLine =~# '^-' && l:previousLine =~# '\m^[^-].*:$'
    let l:indentTo = l:indentTo - shiftwidth()
  endif

  return l:indentTo
endfunction

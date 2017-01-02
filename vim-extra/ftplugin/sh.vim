setlocal makeprg=shellcheck\ -f\ gcc\ %
map <buffer> <leader>fl :make \| cwindow<cr><cr>

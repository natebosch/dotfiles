" Checks whether then file looks like its related to a dart project and sets
" `project_nav_root_markers`
function! s:CheckDartYaml() abort
  let name = expand('%:t')
  if name ==? 'pubspec.yaml' ||
      \ name ==? 'analysis_options.yaml' ||
      \ name ==? 'build.yaml'
    let b:project_nav_root_markers = ['pubspec.yaml']
  endif

  " Pub constraint maps
  if name ==? 'pubspec.yaml'
    nnoremap <leader>dl :read! dartdeps --from=% local<space>
    nnoremap <leader>dp :read! dartdeps latest<space>
    nnoremap <leader>dg :read! dartdeps git<space>
    nnoremap <leader>dr :.! dartdeps --from=% replace<cr>
    xnoremap <leader>dr !dartdeps --from=% replace<cr>
  endif
endfunction

call <SID>CheckDartYaml()

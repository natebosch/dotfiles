" Checks whether then file looks like its related to a dart project and sets
" `project_nav_root_markers`
function! s:CheckDartYaml() abort
  let name = expand('%:t')
  if name ==? 'pubspec.yaml' ||
      \ name ==? 'analysis_options.yaml' ||
      \ name ==? 'build.yaml'
    let b:project_nav_root_markers = ['pubspec.yaml']
  endif
endfunction

call <SID>CheckDartYaml()

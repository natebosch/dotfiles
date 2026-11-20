scriptencoding utf-8

if !exists('s:popups')
  let s:popups = {}
endif

function! git_sticky#enable() abort
  if !has('popupwin') || !exists('##WinScrolled')
    return
  endif

  hi default GitStickyHeader ctermbg=236 guibg=#303030
      \ ctermfg=003 guifg=#C4A000 cterm=bold gui=bold
  hi default GitStickySubname ctermbg=236 guibg=#303030
      \ ctermfg=006 guifg=#06989A

  if empty(prop_type_get('git_sticky_sub'))
    call prop_type_add('git_sticky_sub', {'highlight': 'GitStickySubname'})
  endif

  augroup GitStickyHeader
    autocmd!
    autocmd WinScrolled,WinResized,VimResized,BufWinEnter,BufEnter,WinEnter,
        \TabEnter,BufWinLeave * call git_sticky#update_all()
    autocmd WinClosed *
        \ call git_sticky#update_all(str2nr(expand('<amatch>')))
  augroup END

  call git_sticky#update_all()
endfunction

function! git_sticky#update_all(...) abort
  if !has('popupwin') || !exists('##WinScrolled')
    return
  endif
  let l:closing = a:0 ? a:1 : 0
  let l:cur_tab = tabpagenr()

  for l:key in keys(s:popups)
    let l:wid = str2nr(l:key)
    if l:wid == l:closing || win_id2win(l:wid) == 0
        \ || win_id2tabwin(l:wid)[0] != l:cur_tab
        \ || getwinvar(l:wid, '&filetype') !=# 'git'
      call s:ClosePopup(l:key)
    endif
  endfor

  for l:wid in gettabinfo(l:cur_tab)[0].windows
    if l:wid == l:closing || win_gettype(l:wid) !=# ''
        \ || getwinvar(l:wid, '&filetype') !=# 'git'
      continue
    endif
    call s:UpdateWindow(l:wid)
  endfor
endfunction

function! s:ClosePopup(key) abort
  if !has_key(s:popups, a:key)
    return
  endif
  let l:pid = remove(s:popups, a:key)
  silent! call popup_close(l:pid)
endfunction

function! s:UpdateWindow(wid) abort
  let l:key = string(a:wid)
  let l:info = {'show': v:false}
  call win_execute(a:wid, 'let l:info = s:InspectWindow()')
  if !l:info.show
    call s:ClosePopup(l:key)
    return
  endif

  let l:pos = win_screenpos(a:wid)
  let l:width = winwidth(a:wid)
  if l:pos[0] <= 0 || l:width <= 0
    call s:ClosePopup(l:key)
    return
  endif

  let l:banner = s:FormatBanner(l:info.fname, l:info.ctx, l:width)
  let l:move_opts = {
      \ 'line': l:pos[0],
      \ 'col': l:pos[1],
      \ 'minwidth': l:width,
      \ 'maxwidth': l:width,
      \ 'pos': 'topleft',
      \ 'fixed': 1,
      \ }

  if has_key(s:popups, l:key)
    let l:pid = s:popups[l:key]
    if !empty(popup_getoptions(l:pid))
      call popup_move(l:pid, l:move_opts)
      call popup_settext(l:pid, [l:banner])
      return
    endif
    unlet s:popups[l:key]
  endif

  let l:create_opts = extend({
      \ 'wrap': 0,
      \ 'zindex': 40,
      \ 'mapping': 0,
      \ 'highlight': 'GitStickyHeader',
      \ 'tabpage': tabpagenr(),
      \ }, l:move_opts)
  let s:popups[l:key] = popup_create([l:banner], l:create_opts)
endfunction

function! s:InspectWindow() abort
  let l:top = line('w0')
  if l:top <= 1
    return {'show': v:false}
  endif
  let l:view = winsaveview()
  try
    call cursor(l:top, 1)
    let l:diff_lnum = search('^diff --\%(git\|cc\|combined\) ', 'bcnW')
    if l:diff_lnum <= 0 || l:diff_lnum >= l:top
      return {'show': v:false}
    endif
    let l:commit_lnum = search('^commit \x\+', 'bcnW', l:diff_lnum)
    if l:commit_lnum >= l:diff_lnum
      return {'show': v:false}
    endif
    let l:fname = s:ExtractFilename(l:diff_lnum)
    if l:fname ==# ''
      return {'show': v:false}
    endif
    let l:hunk_lnum = search('^@@\+ [^@]\+ @@', 'bcnW', l:diff_lnum)
    let l:ctx = ''
    if l:hunk_lnum > 0 && l:hunk_lnum < l:top
      let l:ctx = matchstr(getline(l:hunk_lnum), '^@@\+ [^@]\+ @@\s*\zs.*$')
    endif
    return {'show': v:true, 'fname': l:fname, 'ctx': l:ctx}
  finally
    call winrestview(l:view)
  endtry
endfunction

function! s:ExtractFilename(diff_lnum) abort
  let l:limit = min([a:diff_lnum + 6, line('$')])
  let l:old = ''
  for l:lnum in range(a:diff_lnum + 1, l:limit)
    let l:line = getline(l:lnum)
    if l:line =~# '^diff --' || l:line =~# '^@@'
      break
    endif
    if l:line =~# '^--- a/'
      let l:old = matchstr(l:line, '^--- a/\zs.*$')
    elseif l:line =~# '^+++ b/'
      return matchstr(l:line, '^+++ b/\zs.*$')
    elseif l:line ==# '+++ /dev/null' && l:old !=# ''
      return l:old . ' (deleted)'
    elseif l:line =~# '^rename to '
      return matchstr(l:line, '^rename to \zs.*$')
    endif
  endfor

  let l:header = getline(a:diff_lnum)
  if l:header =~# '^diff --git '
    let l:name = matchstr(l:header, '^diff --git a/.\{-} b/\zs.*$')
    if l:name !=# ''
      return l:name
    endif
    return matchstr(l:header, '^diff --git \S\+ \zs\S\+$')
  endif
  if l:header =~# '^diff --\%(cc\|combined\) '
    return matchstr(l:header, '^diff --\%(cc\|combined\) \zs.*$')
  endif
  return ''
endfunction

function! s:FormatBanner(fname, ctx, width) abort
  let l:left = ' ' . a:fname
  if a:ctx ==# '' || strdisplaywidth(l:left) >= a:width
    return {'text': s:TruncateDisplay(l:left, a:width), 'props': []}
  endif
  let l:sep = '  '
  let l:sub = '@@ ' . a:ctx
  let l:rem = a:width - strdisplaywidth(l:left . l:sep)
  if l:rem <= 4
    return {'text': l:left, 'props': []}
  endif
  let l:sub = s:TruncateDisplay(l:sub, l:rem)
  return {
      \ 'text': l:left . l:sep . l:sub,
      \ 'props': [{
      \   'col': strlen(l:left . l:sep) + 1,
      \   'length': strlen(l:sub),
      \   'type': 'git_sticky_sub',
      \ }],
      \ }
endfunction

function! s:TruncateDisplay(str, max_width) abort
  if a:max_width <= 0
    return ''
  endif
  if strdisplaywidth(a:str) <= a:max_width
    return a:str
  endif
  if a:max_width == 1
    return "\u2026"
  endif
  let l:target = a:max_width - 1
  let l:nchars = strchars(a:str)
  while l:nchars > 0
    let l:cand = strcharpart(a:str, 0, l:nchars)
    if strdisplaywidth(l:cand) <= l:target
      return l:cand . "\u2026"
    endif
    let l:nchars -= 1
  endwhile
  return "\u2026"
endfunction

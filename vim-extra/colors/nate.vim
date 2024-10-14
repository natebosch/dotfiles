hi clear
if exists('syntax_on')
  syntax reset
endif

let g:colors_name = 'nate'

hi Normal       guifg=#dbdbdb guibg=#212325

hi ColorColumn  ctermbg=234  guibg=#1c1c1c
hi Comment      ctermfg=067  guifg=#5f87af
hi Constant     ctermfg=166  guifg=#fc8e00
hi CursorColumn ctermbg=236  guibg=#303030
hi Directory    ctermfg=031  guifg=#0087af
hi Error        ctermbg=125  guibg=#af0000
hi FoldColumn   ctermbg=234  guibg=#1c1c1c
hi FoldColumn   ctermfg=075  guifg=#5fafff
hi Function     ctermfg=109  guifg=#01cab1
hi Identifier   ctermfg=032  guifg=#00a3ee
hi Ignore       ctermfg=245  guifg=#8a8a8a
hi LineNr       ctermfg=241  guifg=#999999
hi MatchParen   ctermbg=NONE guibg=NONE cterm=bold,underline gui=bold,underline
hi MatchParen   ctermfg=199  guifg=#ff00af
hi Pmenu        ctermbg=236  guibg=#303030
hi Pmenu        ctermfg=NONE guifg=NONE
hi PmenuSBar    ctermbg=237  guibg=#3a3a3a
hi PmenuSel     ctermbg=241  guibg=#626262
hi PmenuSel     ctermfg=NONE guifg=NONE
hi PmenuThumb   ctermbg=234  guibg=#1c1c1c
hi PreProc      ctermfg=033  guifg=#00a3ee
hi QuickFixLine ctermbg=236  guibg=#303030
hi QuickFixLine ctermfg=253  guifg=#dadada cterm=bold gui=bold
hi Search       ctermbg=063  guibg=#5f5fff
hi SignColumn   ctermbg=234  guibg=#1c1c1c
hi Special      ctermfg=177  guifg=#ad75ff
hi SpellBad     ctermbg=237  guibg=#3a3a3a
hi SpellBad     ctermfg=196  guifg=#ff0000
hi SpellCap     ctermbg=237  guibg=#3a3a3a
hi Statement    ctermfg=179  guifg=#c4a000
hi Statement    term=bold    gui=bold
hi Title        ctermfg=027  cterm=bold guifg=#005fff
hi Type         ctermfg=245  guifg=#a1a1a1
hi Typedef      ctermfg=034  guifg=#00b19c
hi Underlined   ctermfg=177  guifg=#87d7ff
hi Visual       ctermbg=NONE guibg=NONE cterm=bold,reverse   gui=bold,reverse


hi clear ErrorMsg
hi link ErrorMsg Error
hi clear Folded
hi link Folded FoldColumn
hi clear StorageClass
hi link StorageClass Typedef
hi clear Todo
hi link Todo Comment

hi DiffAdd      ctermbg=022  guibg=#005f00 cterm=bold gui=bold
hi DiffChange   ctermbg=022  guibg=#005f00 cterm=bold gui=bold
hi DiffText     ctermbg=033  guibg=#0087ff
hi DiffText     ctermfg=234  guifg=#1c1c1c
hi DiffDelete   ctermbg=052  guibg=#5f0000

" Git diff in commit message editing
hi diffAdded    ctermfg=002  guifg=#4E9A06
hi diffRemoved  ctermfg=001  guifg=#CC0000
hi diffFile     ctermfg=003  guifg=#C4A000
hi diffLine     ctermfg=006  guifg=#06989A
hi diffSubname  ctermfg=005  guifg=#75507B

" Statusline/tabline

hi StatusLine   ctermbg=236  guibg=#303030 cterm=NONE gui=NONE
hi StatusLineNC ctermbg=236  guibg=#303030 cterm=NONE gui=NONE
hi StatusLineNC ctermfg=244  guifg=#808080
hi! link StatusLineTerm StatusLine
hi! link StatusLineTermNC StatusLineNC

hi StatusNormal ctermfg=234  guifg=#1c1c1c cterm=bold gui=bold
hi StatusInsert ctermfg=234  guifg=#1c1c1c cterm=bold gui=bold
hi StatusVisual ctermfg=234  guifg=#1c1c1c cterm=bold gui=bold
hi StatusSelect ctermfg=234  guifg=#1c1c1c cterm=bold gui=bold
hi StatusNormal ctermbg=033  guibg=#0087ff
hi StatusInsert ctermbg=166  guibg=#d75f00
hi StatusVisual ctermbg=070  guibg=#5faf00
hi StatusSelect ctermbg=076  guibg=#5fd700
hi link StatusTerm StatusInsert

hi TabLineFill  ctermbg=236  guibg=#303030 cterm=NONE gui=NONE
hi TabLine      ctermbg=236  guibg=#303030 cterm=NONE gui=NONE
hi TabLine      ctermfg=250  guifg=#bcbcbc

hi VertSplit    ctermbg=NONE cterm=NONE guibg=NONE gui=NONE
hi VertSplit    ctermfg=236  guifg=#303030

let g:terminal_ansi_colors = [
    \ '#3a3a3a', '#ff4593', '#8da600', '#de7c00',
    \ '#00a3ee', '#ad75ff', '#00b19c', '#c7c7c7',
    \ '#6a6a6a', '#ff7daa', '#a1bd00', '#fc8e00',
    \ '#47b9ff', '#bd96ff', '#01cab1', '#f6f6f6'
    \ ]

hi clear
if exists("syntax_on")
  syntax reset
endif

runtime colors/desert.vim

let g:colors_name = "nate-day"

hi Search ctermbg=054
hi SpellBad ctermbg=237 ctermfg=Red
hi SpellCap ctermbg=237
hi MatchParen ctermbg=None ctermfg=199 cterm=bold,underline
hi Pmenu ctermbg=LightGrey
hi LineNr ctermfg=DarkGrey
hi ColorColumn ctermbg=236
hi CursorColumn ctermbg=236
hi Special ctermfg=177
hi Underlined ctermfg=177
hi Title ctermfg=27 cterm=bold
hi PreProc ctermfg=33
hi Visual ctermbg=None cterm=bold,reverse

hi clear Todo
hi link Todo Comment

hi DiffAdd ctermbg=26 cterm=bold
hi DiffChange ctermbg=53 cterm=bold
hi DiffText ctermbg=1 ctermfg=7

" Statusline/tabline

hi StatusLine ctermbg=236 cterm=NONE
hi StatusLineNC ctermbg=236 ctermfg=244 cterm=NONE

hi StatusNormal cterm=bold ctermfg=234 ctermbg=33
hi StatusVisual cterm=bold ctermfg=234 ctermbg=70
hi StatusInsert cterm=bold ctermfg=234 ctermbg=166
hi StatusSelect cterm=bold ctermfg=234 ctermbg=76

hi TabLineFill ctermbg=236 cterm=None
hi TabLine ctermbg=236 ctermfg=250 cterm=None

hi VertSplit ctermbg=None ctermfg=236 cterm=None

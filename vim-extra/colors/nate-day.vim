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
hi Special ctermfg=177
hi Underlined ctermfg=177
hi Title ctermfg=27 cterm=bold
hi PreProc ctermfg=33
hi Visual ctermbg=None cterm=bold,reverse

hi DiffAdd ctermbg=26 cterm=bold
hi DiffChange ctermbg=53 cterm=bold
hi DiffText ctermbg=1 ctermfg=7

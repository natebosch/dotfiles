set nocompatible " vim > vi. Required for other stuff in here
filetype off

""""""""""""""""""""""""""""""""""
""" Vundle Plugins
set rtp+=~/.vim/bundle/Vundle.vim
call vundle#begin()

Plugin 'VundleVim/Vundle.vim'
Plugin 'Valloric/YouCompleteMe'
Plugin 'dart-lang/dart-vim-plugin'
Plugin 'easymotion/vim-easymotion'
Plugin 'tpope/vim-fugitive'
Plugin 'airblade/vim-gitgutter'
Plugin 'tpope/vim-vinegar'
Plugin 'ctrlpvim/ctrlp.vim'
Plugin 'FelikZ/ctrlp-py-matcher'
Plugin 'tpope/vim-surround'
Plugin 'bling/vim-airline'

call vundle#end()
"""""""""""""""""""""""""""""""""


"""""""""""""""""""""""""""""""""
""" Convenience

let g:mapleader=" "

" File Backups
set nobackup " Use git instead!
set directory=$DOTDIR/.vim/tmp/ " Swap file location

" Commands
set history=300 " keep 300 lines of command line history
set showcmd     " display incomplete commands

" Use spaces not tabs
set tabstop=2
set shiftwidth=2
set expandtab
set smarttab

" read a file when it is changed from outside vi
set autoread

" Search
set ignorecase  " Case insensitive search
set smartcase   " Well, sometimes case sensitive
set magic       " Allow pattern matching wish special chars
set incsearch   " do incremental searching
set nowrapscan  " Don't wrap, jump up with gg to keep searching
" Replace
set gdefault    " I usually use /g anyway

" Faster searching
" The Silver Searcher
if executable('ag')
  " Use ag over grep
  set grepprg=ag\ --nogroup\ --nocolor

  " Use ag in CtrlP for listing files. Lightning fast and respects .gitignore
  let g:ctrlp_user_command = 'ag %s -l --nocolor --nogroup --depth 10 -g ""'

  " ag is fast enough that CtrlP doesn't need to cache
  let g:ctrlp_use_caching = 0
endif
let g:ctrlp_match_func = { 'match': 'pymatcher#PyMatch' }

" Find word under cursor
nnoremap <leader>G :grep! "\b<C-R><C-W>\b"<CR>:cw<CR>

" Command completion
set wildmenu
set wildmode=longest:full,full

augroup convenience
  " Nicer handling of comments
  autocmd FileType,BufNewFile,BufWinEnter * setlocal formatoptions-=o
        \ formatoptions+=jqn
  " When editing a file, always jump to the last known cursor position.
  autocmd BufReadPost *
        \ if line("'\"") > 1 && line("'\"") <= line("$") |
        \   exe "normal! g`\"" |
        \ endif
augroup END

" Allow backspacing over everything in insert mode
set backspace=indent,eol,start

" Be able to undo ctrl-u
inoremap <c-u> <c-g>u<c-u>

" Prefer jumping right to a mark over a line and ' is easier to reach.
nnoremap ' `
nnoremap ` '

" Don't really need <c-p> for ctrlp.
let g:ctrlp_map = ''
noremap <C-P> <Tab>

" Only use paste mode once
augroup paste
  autocmd!
  autocmd InsertLeave * set nopaste
augroup END

" Fugitive sometimes likes horizontal diffs
set diffopt+=vertical
"""""""""""""""""""""""""""""""



""""""""""""""""""""""""""""""
""" Skin

" Line numbers
set number
set numberwidth=4
highlight LineNr ctermfg=grey
function! g:ToggleNuMode()
    if(&rnu == 1)
        set nonu
        set nornu
    elseif (&nu == 1)
        set rnu
    else
        set nu
    endif
endfunc
nnoremap \ :call g:ToggleNuMode()<CR>

set scrolloff=3 " Always show at lease 3 lines above/below cursor
set ruler       " show the cursor position all the time

" Colors
syntax on
set hlsearch
nnoremap - :noh<cr>
set background=dark " easier on the eyes
colorscheme desert
hi Search ctermbg=054
hi SpellBad ctermbg=237 ctermfg=Red
hi SpellCap ctermbg=237
hi MatchParen cterm=bold,reverse
syn match EvilSpace " \+$" containedin=ALL " error for trailing spaces
hi link EvilSpace Error
hi Pmenu ctermbg=LightGrey
hi LineNr ctermfg=DarkGrey

" toggle spell check with +
nnoremap + :set spell!<CR>
" Check the highlight group under cursor
map <leader>? :echo "hi<" . synIDattr(synID(line("."),col("."),1),"name") . '> trans<'
\ . synIDattr(synID(line("."),col("."),0),"name") . "> lo<"
\ . synIDattr(synIDtrans(synID(line("."),col("."),1)),"name") . ">"<CR>
" See tabs
set list
set listchars=tab:>-


if exists('+colorcolumn')
    set colorcolumn=+1
    highlight ColorColumn ctermbg=Grey
endif

""" Characters past textwidth become red
""" off by default, toggle with _
if exists("*matchadd")
    highlight OverLength ctermbg=darkred ctermfg=white guibg=#592929
    nnoremap <silent> _
          \ :if exists('w:long_line_match') <Bar>
          \   silent! call matchdelete(w:long_line_match) <Bar>
          \   unlet w:long_line_match <Bar>
          \ elseif &textwidth > 0 <Bar>
          \   let w:long_line_match =
          \       matchadd('OverLength', '\%>'.&tw.'v.\+', -1) <Bar>
          \ else <Bar>
          \   let w:long_line_match =
          \       matchadd('OverLength', '\%>80v.\+', -1) <Bar>
          \ endif<CR>
endif

" Status line
set laststatus=2
let g:airline_theme='bubblegum'
let g:airline_symbols_branch = ''
let g:airline_left_sep = ''
let g:airline_right_sep = ''
"""""""""""""""""""""""""""""



"""""""""""""""""""""""""""""
""" Tools

" Move lines or blocks of text up/down
nnoremap <C-j> :m .+1<CR>==
nnoremap <C-k> :m .-2<CR>==
inoremap <C-j> <Esc>:m .+1<CR>==gi
inoremap <C-k> <Esc>:m .-2<CR>==gi
vnoremap <C-j> :m '>+1<CR>gv=gv
vnoremap <C-k> :m '<-2<CR>gv=gv

" Easier indent visual blocks
vnoremap > >gv
vnoremap < <gv

" Code folding
set foldmethod=indent
set foldnestmax=10
set nofoldenable " don't fold by default
set foldlevel=1

" Explorer
let g:netrw_liststyle=3 " Default to 'NerdTree' style explorer
let g:netrw_altfile=1 " Don't jump to netrw with <c-^>
nnoremap <leader>O :Tex<cr>
nnoremap <leader>o :Ex<cr>
nnoremap <leader>p :CtrlP<cr>

" Buffers
nnoremap <leader>k :CtrlPBuffer<cr>
set hidden
" jump to previous file
map <leader>' <c-^>


""" YCM
" shortcuts
nmap <c-]> :YcmCompleter GoToDefinition<CR>
" Diagnostics but no in the gutter
let g:ycm_show_diagnostics_ui = 1
let g:ycm_enable_diagnostic_signs = 0
let g:ycm_enable_diagnostic_highlighting = 1
let g:ycm_echo_current_diagnostic = 1
" Aggressive completion
let g:ycm_collect_identifiers_from_comments_and_strings = 1
let g:ycm_complete_in_comments_and_strings = 1
let g:ycm_filetype_blacklist = {}
let g:ycm_min_num_of_chars_for_completion = 1
let g:ycm_seed_identifiers_with_syntax = 1
set omnifunc=syntaxcomplete#Complete

" Fugitive shortcuts
nnoremap <leader>gs :Gstatus<cr>
nnoremap <leader>gc :Gcommit<cr>
nnoremap <leader>ga :Gcommit -a<cr>
nnoremap <leader>gd :Gdiff<cr>

" Toggle paste mode
nnoremap <leader>y :set paste!<cr>

" Scratch buffer
function! ScratchOpen()
  let scr_bufnr = bufnr('__scratch__')
  if scr_bufnr == -1
    enew
    setlocal filetype=markdown
    setlocal bufhidden=hide
    setlocal nobuflisted
    setlocal buftype=nofile
    setlocal noswapfile
    file __scratch__
  else
    execute 'buffer ' . scr_bufnr
  endif
endfunction
nnoremap <leader>s :call ScratchOpen()<cr>
""""""""""""""""""""""""""""""""""""""


""""""""""""""""""""""""""""""""""""""
""" Filetype specific

augroup widths
  autocmd FileType text setlocal textwidth=79
  autocmd FileType java setlocal textwidth=100
augroup END

""" Java
" Eclim completions with YCM
let g:EclimCompletionMethod = 'omnifunc'
augroup java
  autocmd FileType java map <buffer> <c-]> :JavaSearchContext -a edit<cr>
augroup END

""""""""""""""""""""""""""""""""""""""



"""""""""""""""""""""""""""""""""""""
""" Local customizations
if filereadable(glob("~/.localvimrc"))
  source ~/.localvimrc
endif
"""""""""""""""""""""""""""""""""""""

" This goes last
filetype plugin indent on

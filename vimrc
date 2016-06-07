set nocompatible " vim > vi. Required for other stuff in here
set rtp+=~/.vim-extra/
filetype off

" Load vim-plug
if empty(glob("~/.vim/autoload/plug.vim"))
    execute '!curl -fLo ~/.vim/autoload/plug.vim --create-dirs
          \ https://raw.github.com/junegunn/vim-plug/master/plug.vim'
endif

""""""""""""""""""""""""""""""""""
""" Vim-plug
call plug#begin('~/.vim/plugged')

function! BuildYCM(info)
  if a:info.status == 'installed' || a:info.force
    !./install.sh
  endif
endfunction

Plug 'Valloric/YouCompleteMe', { 'frozen': 1, 'do': function('BuildYCM') }
Plug 'dart-lang/dart-vim-plugin'
Plug 'justinmk/vim-sneak'
Plug 'tpope/vim-fugitive'
Plug 'airblade/vim-gitgutter'
Plug 'tpope/vim-vinegar'
Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --all' }
Plug 'junegunn/fzf.vim'
Plug 'junegunn/vim-peekaboo'
Plug 'tpope/vim-surround'
Plug 'vim-airline/vim-airline' | Plug 'vim-airline/vim-airline-themes'
Plug 'nelstrom/vim-qargs'
Plug 'wellle/tmux-complete.vim'
Plug 'Raimondi/delimitMate'
Plug 'schickling/vim-bufonly'
Plug 'tmux-plugins/vim-tmux'
Plug 'christoomey/vim-tmux-navigator'
Plug 'SirVer/ultisnips' | Plug 'honza/vim-snippets'
Plug '~/.dartlang-snippets'
Plug '~/.ng2-snippets'
Plug 'tmux-plugins/vim-tmux-focus-events'
Plug 'tpope/vim-repeat'
Plug 'leafgarland/typescript-vim'
Plug 'mbbill/undotree'
Plug 'jreybert/vimagit'
Plug 'zirrostig/vim-schlepp'
Plug 'chip/vim-fat-finger'
Plug 'davidbeckingsale/writegood.vim'
Plug 'ap/vim-css-color'
Plug 'christoomey/vim-sort-motion'
Plug 'mjbrownie/swapit'
Plug 'wellle/targets.vim'

call plug#end()
"""""""""""""""""""""""""""""""""


"""""""""""""""""""""""""""""""""
""" Convenience

let g:mapleader=" "

" File Backups
set nobackup " Use git instead!
let swpdir = expand("~/.vim/swp")
if !isdirectory(swpdir)
  call mkdir(swpdir)
endif
set directory=~/.vim/swp/

" Persistent Undo
let undodir = expand("~/.vim/undo")
if !isdirectory(undodir)
  call mkdir(undodir)
endif
set undodir=~/.vim/undo
set undofile

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
endif

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

" Easier indent visual blocks
vnoremap > >gv
vnoremap < <gv

" Prefer jumping right to a mark over a line and ' is easier to reach.
nnoremap ' `
nnoremap ` '

" Only use paste mode once
augroup paste
  autocmd!
  autocmd InsertLeave * set nopaste
augroup END

" Fugitive sometimes likes horizontal diffs
set diffopt+=vertical
" Avoid piling up fugitive buffers
autocmd BufReadPost fugitive://* set bufhidden=delete

" Complete from tmux panes in YCM
let g:tmuxcomplete#trigger = 'omnifunc'

" delimitMate
let delimitMate_expand_cr = 2
let delimitMate_expand_inside_quotes = 1
let delimitMate_jump_expansion = 0
let delimitMate_excluded_regions = 0

" Code folding
set foldmethod=indent
set foldnestmax=10
set foldlevel=99

" Explorer
let g:netrw_liststyle=3 " Default to 'NerdTree' style explorer
let g:netrw_altfile=1 " Don't jump to netrw with <c-^>

" Allow hiding unsaved buffers
set hidden

""" YCM
" Diagnostics but not in the gutter
let g:ycm_show_diagnostics_ui = 1
let g:ycm_enable_diagnostic_signs = 0
let g:ycm_enable_diagnostic_highlighting = 1
let g:ycm_echo_current_diagnostic = 1
let g:ycm_always_populate_location_list = 1
" Aggressive completion
let g:ycm_collect_identifiers_from_comments_and_strings = 1
let g:ycm_complete_in_comments_and_strings = 1
let g:ycm_filetype_blacklist = {}
let g:ycm_min_num_of_chars_for_completion = 1
let g:ycm_seed_identifiers_with_syntax = 1

" Peekaboo
let g:peekaboo_window = 'vertical botright 50new'
let g:peekaboo_delay = 750

" Ultisnips
let g:UltiSnipsExpandTrigger="<tab>"
let g:UltiSnipsJumpForwardTrigger="<tab>"
let g:UltiSnipsJumpBackwardTrigger="<c-z>"
let g:UltiSnipsListSnippets="<c-t>"
" Fix YCM completion of snippets
let g:UltiSnipsUsePythonVersion = 2
" Don't let YCM steal <tab> from ultisnips
let g:ycm_key_list_select_completion = ['<C-n>', '<Down>']
let g:ycm_key_list_previous_completion = ['<C-p>', '<Up>']

" Don't restore cursor to the middle of a git commit
au FileType gitcommit au! BufEnter COMMIT_EDITMSG 
      \ call setpos('.', [0, 1, 1, 0])
"""""""""""""""""""""""""""""""



""""""""""""""""""""""""""""""
""" Skin

" Line numbers
set number
set numberwidth=4

set scrolloff=3 " Always show at lease 3 lines above/below cursor
set ruler       " show the cursor position all the time

" Colors
syntax on
set hlsearch
set background=dark " easier on the eyes
colorscheme nate-day
syn match EvilSpace " \+$" containedin=ALL " error for trailing spaces
hi link EvilSpace Error

" See tabs
set list
set listchars=tab:>-


if exists('+colorcolumn')
    set colorcolumn=+1
endif

" Status line
set laststatus=2
let g:airline_theme='bubblegum'
let g:airline_symbols_branch = ''
let g:airline_left_sep = ''
let g:airline_right_sep = ''
"""""""""""""""""""""""""""""



"""""""""""""""""""""""""""""
""" Tools/Key Maps

" Find word under cursor
nnoremap <leader>G :grep! "\b<C-R><C-W>\b"<CR>:cw<CR>

" Visual Tweaks
nnoremap <leader>vn :set number!<CR>
nnoremap <leader>vs :set spell!<CR>
nnoremap - :noh<cr>
nnoremap <leader>vu :UndotreeToggle<cr>
" Check the highlight group under cursor
nnoremap <leader>v? :echo "hi<" . synIDattr(synID(line("."),col("."),1),"name")
      \ . '> trans<' . synIDattr(synID(line("."),col("."),0),"name") . "> lo<"
      \ . synIDattr(synIDtrans(synID(line("."),col("."),1)),"name") . ">"<CR>
" Toggle error matching for past width
if exists("*matchadd")
    hi link OverLength Error
    nnoremap <silent> <leader>vl
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

" Move lines or blocks of text up/down
vmap <C-h> <Plug>SchleppLeft
vmap <C-j> <Plug>SchleppDown
vmap <C-k> <Plug>SchleppUp
vmap <C-l> <Plug>SchleppRight
vmap D <Plug>SchleppDup
inoremap <C-j> <Esc>:m .+1<CR>==gi
inoremap <C-k> <Esc>:m .-2<CR>==gi

" File/Buffer/Code navigation
nnoremap <leader>o :Ex<cr>
nnoremap <leader>lf :Files<cr>
nnoremap <leader>lr :History<cr>
nnoremap <leader>lb :Buffers<cr>
nnoremap <leader>ll :Lines<cr>
nnoremap gd :YcmCompleter GoToDefinition<CR>
nnoremap <leader>x :bp\|bd #<cr>

" Fuzzy history search
nnoremap <leader>: :History:<cr>
nnoremap <leader>/ :History/<cr>

" Git shortcuts
nnoremap <leader>gs :Gstatus<cr>
nnoremap <leader>gc :Gcommit<cr>
nnoremap <leader>ga :Gcommit -a<cr>
nnoremap <leader>gd :Gdiff<cr>
nnoremap <leader>gl :Commits<cr>
nnoremap <leader>gp :MagitOnly<cr>

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



"""""""""""""""""""""""""""""""""""""
""" Local customizations
if filereadable(glob("~/.vimrc.local"))
  source ~/.vimrc.local
endif
"""""""""""""""""""""""""""""""""""""

" This goes last
filetype plugin indent on

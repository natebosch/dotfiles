set nocompatible " vim > vi. Required for other stuff in here
set nomodeline
set rtp+=~/.vim-extra/
filetype off

" Load vim-plug
if empty(glob("~/.vim/autoload/plug.vim"))
  execute '!curl -fLo ~/.vim/autoload/plug.vim --create-dirs
      \ https://raw.github.com/junegunn/vim-plug/master/plug.vim'
endif

""""""""""""""""""""""""""""""""""
""" Vim-plug
function! DevPlugin(name) abort
  let dev = expand('~/projects/'.split(a:name, '/')[1])
  if isdirectory(dev) | return dev | endif
  return a:name
endfunction

call plug#begin('~/.vim/plugged')

" Extend default behavior / Stay out of the way
Plug 'tpope/vim-repeat'
Plug 'wellle/targets.vim'
Plug 'michaeljsmith/vim-indent-object'
Plug 'christoomey/vim-sort-motion'
Plug 'scrooloose/nerdtree'
Plug 'Xuyuanp/nerdtree-git-plugin'
Plug 'ap/vim-css-color'
Plug 'chip/vim-fat-finger'
Plug 'junegunn/vim-peekaboo'
Plug 'kana/vim-textobj-user' | Plug 'Julian/vim-textobj-variable-segment'
Plug 'xtal8/traces.vim'
Plug 'andymass/vim-matchup'

" Navigation/Organization
Plug 'christoomey/vim-tmux-navigator'
Plug 'tmux-plugins/vim-tmux-focus-events'
Plug 'schickling/vim-bufonly'

" Tools
Plug 'jreybert/vimagit'
Plug 'tpope/vim-fugitive'
Plug 'zirrostig/vim-schlepp'
Plug 'mjbrownie/swapit'
Plug 'tpope/vim-surround'
Plug 'justinmk/vim-sneak'
Plug 'Raimondi/delimitMate'
Plug 'tommcdo/vim-exchange'
if has('python3')
  Plug 'SirVer/ultisnips'
endif
Plug DevPlugin('natebosch/vim-lsc')
Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --all' }
Plug 'junegunn/fzf.vim'
Plug 'tpope/vim-eunuch'
Plug 'haya14busa/vim-poweryank'

" Filetype
Plug 'tmux-plugins/vim-tmux'
Plug DevPlugin('dart-lang/dart-vim-plugin')
Plug DevPlugin('natebosch/dartlang-snippets')
Plug DevPlugin('natebosch/vim-lsc-dart')
Plug 'tpope/vim-git'

if filereadable(glob("~/.vimrc.local_plugins"))
  source ~/.vimrc.local_plugins
endif

call plug#end()
"""""""""""""""""""""""""""""""""


"""""""""""""""""""""""""""""""""
""" Convenience

let g:mapleader=" "

" File Backups
set nobackup " Use git instead!
set backupcopy=yes
let swpdir = expand("~/.vim/swp")
if !isdirectory(swpdir)
  call mkdir(swpdir)
endif
set directory=~/.vim/swp//

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

" Dump command output to a buffer to avoid  `-- More --`
command! -nargs=1 VC  call <SID>ViewCommand(<q-args>)

function! s:ViewCommand(cmd)
  redir @v
  silent execute a:cmd
  redir END
  new
  set buftype=nofile
  put v
endfunction

" Use spaces not tabs
set tabstop=2
set shiftwidth=2
set expandtab
set smarttab
set nojoinspaces " Don't use a double space after period

" Defaults that apply to most filetypes
set textwidth=80

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
  autocmd!
  " Nicer handling of comments
  autocmd FileType,BufNewFile,BufWinEnter * setlocal formatoptions-=o
      \ formatoptions+=rjqn
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

" Avoid piling up fugitive buffers
augroup fugitive
  autocmd!
  autocmd BufReadPost fugitive://* set bufhidden=delete
augroup END

" delimitMate
let delimitMate_expand_cr = 2
let delimitMate_expand_inside_quotes = 1
let delimitMate_jump_expansion = 0
let delimitMate_excluded_regions = 0

" Code folding
set foldmethod=indent
set foldnestmax=10
set foldlevel=99

" Explore
let g:NERDTreeHijackNetrw=1
let g:NERDTreeMapJumpNextSibling="<C-n>"
let g:NERDTreeMapJumpPrevSibling="<C-p>"

" Allow hiding unsaved buffers
set hidden

""" LSC
let g:lsc_auto_map = v:true

" FZF
let g:fzf_commits_log_options = '--graph --color=always '
    \.'--pretty=format:"%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%cr) '
    \.'%C(bold blue)<%an>%Creset"'
command! -bang -nargs=? -complete=dir Files
  \ call fzf#vim#files(<q-args>, fzf#vim#with_preview(), <bang>0)

" Peekaboo
let g:peekaboo_window = 'vertical botright 50new'
let g:peekaboo_delay = 750

" Ultisnips
let g:UltiSnipsExpandTrigger="<tab>"
let g:UltiSnipsJumpForwardTrigger="<tab>"
let g:UltiSnipsJumpBackwardTrigger="<c-z>"

" Split direction
set splitright
set splitbelow

" Automatically close preview window after completing an item, only if the
" preview window wasn't open before completing.
augroup PREVIEW_AUTOCLOSE
  autocmd!
  autocmd User LSCAutocomplete let g:was_preview_open = <SID>IsPreviewOpen()
  autocmd CompleteDone * call <SID>ClosePreview()
augroup END
function! s:IsPreviewOpen() abort
  for win in range(1, winnr('$'))
    if getwinvar(win, '&previewwindow')
      return v:true
    endif
  endfor
  return v:false
endfunction
function! s:ClosePreview() abort
  if !exists('g:was_preview_open') || !g:was_preview_open
    silent! pclose
  endif
endfunction

" Hide "Back at Original" and other completion messages
set shortmess+=c
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

" See tabs and trailing spaces
set list
set listchars=tab:>-,trail:·

if exists('+colorcolumn')
  set colorcolumn=+1
endif

set belloff=cursor,esc,wildmode,error

set diffopt=filler,vertical
if has('nvim-0.3.2') || has('patch-8.1.0360')
    set diffopt+=internal,algorithm:histogram,indent-heuristic
endif

set fillchars=diff:\ ,vert:┃

if has('nvim')
  set inccommand=split
endif
"""""""""""""""""""""""""""""



"""""""""""""""""""""""""""""
""" Tools/Key Maps

" Visual Tweaks
nnoremap <leader>vn :set number!<CR>
nnoremap <leader>vs :set spell!<CR>
nnoremap - :noh<cr>
" Check the highlight group under cursor
nnoremap <leader>v? :echo "hi<" . synIDattr(synID(line("."),col("."),1),"name")
    \ . '> trans<' . synIDattr(synID(line("."),col("."),0),"name") . "> lo<"
    \ . synIDattr(synIDtrans(synID(line("."),col("."),1)),"name") . ">"<CR>

" Move lines or blocks of text up/down
vmap <C-h> <Plug>SchleppLeft
vmap <C-j> <Plug>SchleppDown
vmap <C-k> <Plug>SchleppUp
vmap <C-l> <Plug>SchleppRight
vmap D <Plug>SchleppDup
inoremap <C-j> <Esc>:m .+1<CR>==gi
inoremap <C-k> <Esc>:m .-2<CR>==gi

" File/Buffer/Code navigation
nnoremap <leader>o :edit %:h<cr>
nnoremap <leader>lf :Files<cr>
nnoremap <leader>lr :History<cr>
nnoremap <leader>lb :Buffers<cr>
nnoremap <leader>lq :QuickFix<cr>
nnoremap <leader>ll :LocationList<cr>
nnoremap <leader>lh :Helptags<cr>
nnoremap <leader>x :bp\|bd #<cr>

nnoremap <leader>gt :execute 'tabn '.g:lasttab<cr>
if !exists('g:lasttab')
  let g:lasttab = 1
endif
augroup LastTab
  autocmd!
  autocmd TabLeave * let g:lasttab = tabpagenr()
augroup END

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
nnoremap <leader>p :set paste!<cr>
map <leader>y <Plug>(operator-poweryank-osc52)

" Rename word or selection, dot to repeat
nnoremap <silent> <leader>R *Ncgn
vnoremap <silent> <leader>R "zy/<c-r>z<cr>Ncgn

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

" Word highlighting
hi Word1 ctermbg=red ctermfg=black
hi Word2 ctermbg=blue ctermfg=black
hi Word3 ctermbg=yellow ctermfg=black
nnoremap <leader>ha :call AddMatchWord('Word1')<cr>
nnoremap <leader>hA :call ClearMatchWord('Word1')<cr>
nnoremap <leader>hs :call AddMatchWord('Word2')<cr>
nnoremap <leader>hS :call ClearMatchWord('Word2')<cr>
nnoremap <leader>hd :call AddMatchWord('Word3')<cr>
nnoremap <leader>hD :call ClearMatchWord('Word3')<cr>
nnoremap <leader>hh :call ClearMatchWords()<cr>

function! ClearMatchWords()
  for l:i in [1, 2, 3]
    call ClearMatchWord('Word'.l:i)
  endfor
endfunction

function! ClearMatchWord(group)
  if exists('w:matched_words') && exists('w:matched_words["'.a:group.'"]')
    call matchdelete(w:matched_words[a:group])
    unlet w:matched_words[a:group]
  endif
endfunction

function! AddMatchWord(group)
  if !exists('w:matched_words')
    let w:matched_words={}
  endif
  call ClearMatchWord(a:group)
  let l:let_statement = 'let w:matched_words["'.a:group.'"] = '
  exe printf(l:let_statement.'matchadd("'.a:group.'", '."'\\<%s\\>')",
      \escape(expand('<cword>'), '/\'))
endfunction
""""""""""""""""""""""""""""""""""""""



"""""""""""""""""""""""""""""""""""""
""" Local customizations
if filereadable(glob("~/.vimrc.local"))
  source ~/.vimrc.local
endif
"""""""""""""""""""""""""""""""""""""

" This goes last
filetype plugin indent on

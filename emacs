;; Sourced entirely (for now) from http://blog.aaronbieber.com/2015/05/24/from-vim-to-emacs-in-fourteen-days.html

(require 'package)

(add-to-list 'package-archives '("org" . "http://orgmode.org/elpa/"))
(add-to-list 'package-archives '("melpa" . "http://melpa.org/packages/"))
(add-to-list 'package-archives '("melpa-stable" . "http://stable.melpa.org/packages/"))
(add-to-list 'package-archives '("marmalade" . "https://marmalade-repo.org/packages/")) ; dart-mode



(setq package-enable-at-startup nil)

(defun ensure-package-installed (&rest packages)
  "Assure every package is installed, ask for installation if it’s not.

Return a list of installed packages or nil for every skipped package."
  (mapcar
   (lambda (package)
     (if (package-installed-p package)
         nil
       (if (y-or-n-p (format "Package %s is missing. Install it? " package))
           (package-install package)
         package)))
   packages))

;; Make sure to have downloaded archive description.
(or (file-exists-p package-user-dir)
    (package-refresh-contents))

;; Activate installed packages
(package-initialize)

;; The packages I was told to use
(ensure-package-installed 'evil      ;vim
			  'evil-leader
			  'dart-mode 
			  'flycheck  ;on the fly syntax checking
                          'helm)     ;fuzzy completion of menus?

;; make it like VIM. I only have so many fingers after all...
(require 'evil)
(evil-mode t)

;; On-the-fly Dart syntax checking
(setq dart-enable-analysis-server t)
(add-hook 'dart-mode-hook 'flycheck-mode)

;; Feel more like vim
(setq make-backup-files nil) ; Don't back things up, depend on source control
(menu-bar-mode -1) ; No toolbar
;; Leader
(require 'evil-leader)
(setq evil-leader/in-all-states 1)
(global-evil-leader-mode)
(evil-leader/set-leader "<SPC>")
(evil-leader/set-key "d" 'execute-extended-command)
;; always allow folding (breaks things)
;;(define-globalized-minor-mode global-hs-minor-mode
;;  hs-minor-mode hs-minor-mode)
;;(global-hs-minor-mode 1)


;; Let Helm take over
(helm-mode 1)
(evil-leader/set-key ";" 'helm-mini) ; buffer browsing?
(setq helm-buffers-fuzzy-matching t)


;; Skin
(global-linum-mode t)
(setq linum-format "%d ")

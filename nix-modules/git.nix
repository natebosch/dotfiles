{ config, userEmail, pkgs, ... }:
{
  programs.git = {
    enable = true;
    ignores = [
      ".ignore"
      "flake.nix"
      "flake.lock"
      ".envrc"
      ".direnv/"
      "**/doc/api/"
      ".gemini/"
    ] ++ (if pkgs.stdenv.isDarwin then [ ".DS_Store" ] else [ ]);

    settings = {
      user = {
        name = "Nate Bosch";
        email = userEmail;
      };
      init = {
        defaultBranch = "main";
        templatedir = "${config.home.homeDirectory}/.git-templates";
      };
      core = {
        pager = "diffr | less -R";
      };
      advice = {
        addIgnoredFile = false;
      };
      interactive = {
        diffFilter = "diffr";
      };
      branch = {
        autosetuprebase = "always";
      };
      color = {
        ui = true;
        diff = {
          meta = "yellow";
          func = "magenta";
        };
        status = {
          added = "green";
          untracked = "red";
          nobranch = "bold red";
          changed = "yellow";
        };
        interactive = {
          prompt = "33";
        };
      };
      alias = {
        st = "status -sb";
        ci = "commit";
        br = "branch";
        co = "checkout";
        f = "fuzzyfixup";
        b = "!f() { git checkout $(fzb) --; }; f";
        bb = "checkout @{-1}";
        lu = "!f() { git push . HEAD:$(git rev-parse --symbolic-full-name @{u}); }; f";
        d = "!f() { vim -R -M -p $(git diff --name-only $@) +\"tabdo Gdiff $@\" +\"tabdo wincmd l\" +tabfirst; }; f";
        lg = "log --graph --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%cr) %C(bold blue)<%an>%Creset' --abbrev-commit --date=relative";
        lga = "!git lg --all";
        lp = "log -p --show-notes=*";
        del = "!git ls-files --deleted | xargs git rm";
        boast = "!git ls-files | egrep -v 'png|min.js|gif|jpg|library' | xargs -n1 git blame -M15 -CCC15 -wfc | sed 's/^[^(]*(\\([a-zA-Z4 ]*\\).*$/\\1/' | sed 's/^ *//;s/ *$//' | sort | uniq -c | sort -n";
        files = "! [ -z \"$GIT_PREFIX\" ] || cd $GIT_PREFIX ; git --no-pager diff @{u} --name-only --relative";
        mainbranch = "![ -n \"$(git branch --list main)\" ] && echo 'main' || echo 'master'";
      };
      "mergetool \"vimdiff3\"" = {
        cmd = "vim -f -d -c \"wincmd J\" \"$MERGED\" \"$LOCAL\" \"$BASE\" \"$REMOTE\"";
      };
      merge = {
        tool = "vimdiff3";
        renamelimit = 10000;
      };
      rebase = {
        autosquash = true;
      };
      diff = {
        algorithm = "histogram";
        wsErrorHighlight = "all";
        renamelimit = 10000;
        whitespace = "diff-algorithm,-all-space";
      };
      credential = {
        helper = "cache --timeout=3600";
      };
      push = {
        default = "simple";
      };
      mergetool = {
        keepBackup = false;
      };
      difftool = {
        prompt = false;
      };
      fetch = {
        prune = true;
      };
      commit = {
        verbose = true;
      };
      pager = {
        branch = false;
      };
      log = {
        mailmap = true;
      };
    };
  };

  home.file.".git-templates".source = ../git-templates;
}

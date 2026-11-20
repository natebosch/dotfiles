# shellcheck shell=bash
# Loads direnv environments into coding-agent bash commands.
#
# Agent commands run in a fresh non-interactive bash, which does not source
# ~/.bashrc and so never runs direnv's hook. Non-interactive bash does source
# $BASH_ENV. Pointing BASH_ENV at this file makes each agent command load the
# direnv environment of its starting directory and reload it after every `cd`,
# matching an interactive shell with direnv's hook. Repo-specific tools from a
# `git enhance` dev shell are then on PATH without a `direnv exec` prefix.
#
# Only directories whose .envrc has been allowed are loaded. Elsewhere direnv
# changes nothing. This file returns immediately outside agent commands, which
# antigravity harnesses mark with ANTIGRAVITY_AGENT=1.

# Nested bash processes inherit the loaded environment; don't reload it in
# each of them.
unset BASH_ENV

if [[ ${ANTIGRAVITY_AGENT:-} == 1 ]] && command -v direnv >/dev/null; then
  _code_harness_direnv_load() {
    eval "$(DIRENV_LOG_FORMAT='' direnv export bash 2>/dev/null)"
  }
  cd() {
    builtin cd "$@" && _code_harness_direnv_load
  }
  _code_harness_direnv_load
fi

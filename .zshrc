# Interactive Zsh only. Start a fresh shell after changing this file.
[[ -o interactive ]] || return 0
[[ -z ${_SHELL_CONFIG_LOADED-} ]] || return 0

typeset -U path fpath
path=("$HOME/.local/bin" "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/shims" $path)

# Machine-specific paths belong here, before tool activation.
if [[ -f ${ZDOTDIR:-$HOME}/.zshrc.local ]]; then
    source "${ZDOTDIR:-$HOME}/.zshrc.local" || return
fi

if (( ! $+commands[mise] )); then
    print -u2 -- 'shell: mise is missing; install mise and run mise install first'
    return 127
fi
_shell_init=$(MISE_OFFLINE=1 mise activate zsh) || return
MISE_OFFLINE=1 eval "$_shell_init" || return

for _shell_tool in starship atuin zoxide wt rg; do
    if (( ! $+commands[$_shell_tool] )); then
        print -u2 -- "shell: missing $_shell_tool; run mise install"
        return 127
    fi
done

export EDITOR=vim
HISTFILE=${ZDOTDIR:-$HOME}/.zsh_history
HISTSIZE=50000
SAVEHIST=50000
setopt APPEND_HISTORY HIST_IGNORE_SPACE HIST_EXPIRE_DUPS_FIRST
bindkey -e

# Ignore insecure completion directories instead of prompting at startup.
autoload -Uz compinit
compinit -i || return

alias l='ls -lah'
alias zs='exec zsh -l'
alias main='wt switch "^"'

rr() {
    local root
    root=$(git rev-parse --show-toplevel) || return
    builtin cd -- "$root"
}

# Keep the frequently used target-before-pattern spelling.
f() {
    if (( $# != 2 )); then
        print -u2 -- 'usage: f <path> <pattern>'
        return 2
    fi
    command rg -- "$2" "$1"
}

_shell_init=$(zoxide init zsh) || return
eval "$_shell_init" || return
_shell_init=$(wt config shell init zsh) || return
eval "$_shell_init" || return
_shell_init=$(atuin init zsh --disable-up-arrow) || return
eval "$_shell_init" || return
_shell_init=$(starship init zsh) || return
eval "$_shell_init" || return

unset _shell_init _shell_tool
typeset -g _SHELL_CONFIG_LOADED=1

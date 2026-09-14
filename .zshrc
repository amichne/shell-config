# Interactive Zsh only. Use `zs` after changes to start a fresh shell.
[[ -o interactive ]] || return 0
[[ -z ${_SHELL_CONFIG_RESULT-} ]] || return "$_SHELL_CONFIG_RESULT"
typeset -g _SHELL_CONFIG_RESULT=0

typeset -U path fpath
if [[ -r ${ZDOTDIR:-$HOME}/.zshpath ]]; then
    _shell_saved_path=$(<"${ZDOTDIR:-$HOME}/.zshpath")
    path=("${(@s/:/)_shell_saved_path}" "${path[@]}")
    unset _shell_saved_path
fi
# Retire old runtime/tool paths even when `zs` inherits them from the old shell.
path=("${(@)path:#$HOME/.sdkman/*}")
path=("${(@)path:#$HOME/.docker/*}")
path=("${(@)path:#$HOME/code/apollo/*}")
path=("${(@)path:#/Applications/VMware Fusion.app/*}")
[[ ${JAVA_HOME-} != "$HOME/.sdkman/"* ]] || unset JAVA_HOME
path=("$HOME/.local/bin" "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/shims" $path)

# Machine-specific additions and preferences, before tool activation.
if [[ -f ${ZDOTDIR:-$HOME}/.zshrc.local ]]; then
    source "${ZDOTDIR:-$HOME}/.zshrc.local" || { _SHELL_CONFIG_RESULT=$?; return "$_SHELL_CONFIG_RESULT"; }
fi

export EDITOR=${EDITOR:-vim}
export VISUAL=${VISUAL:-$EDITOR}
export PAGER=${PAGER:-less}
export LESS=${LESS:--FRiX}
HISTFILE=${ZDOTDIR:-$HOME}/.zsh_history
HISTSIZE=100000
SAVEHIST=100000
setopt APPEND_HISTORY EXTENDED_HISTORY SHARE_HISTORY HIST_IGNORE_SPACE
setopt HIST_EXPIRE_DUPS_FIRST HIST_IGNORE_DUPS HIST_FIND_NO_DUPS HIST_REDUCE_BLANKS
setopt AUTO_CD AUTO_PUSHD PUSHD_IGNORE_DUPS PUSHD_SILENT INTERACTIVE_COMMENTS
unsetopt BEEP FLOW_CONTROL
bindkey -e
bindkey '^[[H' beginning-of-line
bindkey '^[[F' end-of-line
bindkey '^[[3~' delete-char
bindkey '^[[1;5C' forward-word
bindkey '^[[1;5D' backward-word
bindkey '^[b' backward-word
bindkey '^[f' forward-word
# Up/Down search history by the prefix already typed; Ctrl-R belongs to Atuin.
autoload -Uz up-line-or-beginning-search down-line-or-beginning-search
zle -N up-line-or-beginning-search
zle -N down-line-or-beginning-search
bindkey '^[[A' up-line-or-beginning-search
bindkey '^[[B' down-line-or-beginning-search

# Package-manager completion directories must be present before compinit.
for _shell_share in /opt/homebrew/share /usr/local/share /usr/share; do
    for _shell_dir in "$_shell_share/zsh/site-functions" "$_shell_share/zsh-completions"; do
        [[ ! -d $_shell_dir ]] || fpath+=("$_shell_dir")
    done
done
autoload -Uz compinit
# Ignore insecure directories; never disable the security check with compinit -u.
compinit -i || _SHELL_CONFIG_RESULT=1
zmodload zsh/complist
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list '' 'm:{a-zA-Z}={A-Za-z}'
zstyle ':completion:*' group-name ''
zstyle ':completion:*:descriptions' format '%F{yellow}%d%f'
zstyle ':completion:*' verbose yes
zstyle ':completion:*' squeeze-slashes true
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"

alias ls='eza --smart-group --group-directories-first --icons=automatic'
alias l='eza --smart-group --group-directories-first --icons=automatic --all'
alias zs='exec zsh -l'
alias main='wt switch "^"'
rr() {
    local root
    root=$(git rev-parse --show-toplevel) || return
    builtin cd -- "$root"
}
f() {
    if (( $# != 2 )); then
        print -u2 -- 'usage: f <path> <pattern>'
        return 2
    fi
    command rg -- "$2" "$1"
}

# Native prompt remains usable if an integration fails. Startup still returns
# failure and reports the exact boundary; another missing tool cannot hide ZLE.
PROMPT='%F{cyan}%~%f %(?..%F{red}exit:%?%f)\n%# '
PROMPT=${PROMPT/\\n/$'\n'}
_shell_activate() {
    local init_code
    if (( ! $+commands[$1] )); then
        print -u2 -- "shell-config: stage=$1 outcome=missing-command; run ./install.sh migrate from the checkout"
        _SHELL_CONFIG_RESULT=127
        return
    fi
    if init_code=$("$@"); then
        if ! eval "$init_code"; then
            print -u2 -- "shell-config: stage=$1 outcome=activation-failed"
            _SHELL_CONFIG_RESULT=1
        fi
    else
        print -u2 -- "shell-config: stage=$1 outcome=init-failed"
        _SHELL_CONFIG_RESULT=1
    fi
}
MISE_OFFLINE=1 _shell_activate mise activate zsh
if (( $+commands[mise] )); then
    MISE_OFFLINE=1 _shell_activate mise completion zsh
fi
_shell_activate atuin gen-completions --shell zsh
_shell_activate zoxide init zsh
_shell_activate wt config shell init zsh
# fzf provides Ctrl-T files, Alt-C directories, and **<Tab> fuzzy completion.
export FZF_DEFAULT_OPTS=${FZF_DEFAULT_OPTS:---height=40% --layout=reverse --border=rounded}
if [[ -o zle && -t 0 && -t 1 ]]; then
    FZF_CTRL_R_COMMAND='' _shell_activate fzf --zsh
fi
_shell_activate atuin init zsh --disable-up-arrow
export STARSHIP_CONFIG=${STARSHIP_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/starship.toml}
_shell_activate starship init zsh
unfunction _shell_activate

# Small system packages; no framework or plugin manager at shell startup.
# Syntax highlighting must be loaded after completion and every ZLE integration.
ZSH_AUTOSUGGEST_STRATEGY=(history)
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'
for _shell_plugin in zsh-autosuggestions zsh-syntax-highlighting; do
    _shell_plugin_file=''
    for _shell_share in /opt/homebrew/share /usr/local/share /usr/share; do
        if [[ -r $_shell_share/$_shell_plugin/$_shell_plugin.zsh ]]; then
            _shell_plugin_file=$_shell_share/$_shell_plugin/$_shell_plugin.zsh
            break
        fi
    done
    if [[ -n $_shell_plugin_file ]]; then
        if ! source "$_shell_plugin_file"; then
            print -u2 -- "shell-config: stage=$_shell_plugin outcome=source-failed"
            _SHELL_CONFIG_RESULT=1
        fi
    else
        print -u2 -- "shell-config: stage=$_shell_plugin outcome=missing-plugin; install the $_shell_plugin system package"
        _SHELL_CONFIG_RESULT=127
    fi
done
unset _shell_share _shell_dir _shell_plugin _shell_plugin_file
(( _SHELL_CONFIG_RESULT != 0 )) || typeset -g _SHELL_CONFIG_LOADED=1
return "$_SHELL_CONFIG_RESULT"

# Retain the cutover shell's PATH as data, including paths containing spaces.
# Never source/eval this file: directory names are not shell code.
typeset -U path
if [[ -r ${ZDOTDIR:-$HOME}/.zshpath ]]; then
    _shell_saved_path=$(<"${ZDOTDIR:-$HOME}/.zshpath")
    path=("${(@s/:/)_shell_saved_path}" "${path[@]}")
    unset _shell_saved_path
fi
# Shims also work in login shells without interactive prompt hooks.
# Retire old runtime/tool paths even when `zs` inherits them from the old shell.
path=("${(@)path:#$HOME/.sdkman/*}")
path=("${(@)path:#$HOME/.docker/*}")
path=("${(@)path:#$HOME/code/apollo/*}")
path=("${(@)path:#/Applications/VMware Fusion.app/*}")
[[ ${JAVA_HOME-} != "$HOME/.sdkman/"* ]] || unset JAVA_HOME
path=("$HOME/.local/bin" "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/shims" $path)

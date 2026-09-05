# Shims also work in login shells without interactive prompt hooks.
typeset -U path
path=("$HOME/.local/bin" "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/shims" $path)

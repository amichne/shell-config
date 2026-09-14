#!/bin/sh
set -eu

usage() {
    cat <<'USAGE'
Usage: ./install.sh <plan|migrate|activate|restore|toggle|status> [--shell-only]

--shell-only is supported by plan, migrate, and activate; it leaves AI files alone.
Restore and status use the scope recorded in the snapshot.

plan      Preview version-pinned tool provisioning and every managed path without changing them.
migrate   Install the pinned toolchain, then perform the reversible activation.
activate  Snapshot the current config and symlink managed files to this checkout.
restore   Restore the exact pre-activation files from the latest snapshot.
toggle    Restore when active; activate when inactive.
status    Report whether the checkout is active and detect managed-file drift.

State defaults to ${XDG_STATE_HOME:-$HOME/.local/state}/shell-config-cutover.
Override it with SHELL_CONFIG_STATE_DIR.
USAGE
}

fail() {
    printf 'shell-config: %s\n' "$*" >&2
    exit 2
}

copy_preserve() {
    cp -a "$1" "$2" 2>/dev/null || cp -pPR "$1" "$2"
}

reject_multiline() {
    case "$1" in
        *"
"*) fail "$2 contains an unsupported newline" ;;
    esac
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$script_dir
: "${HOME:?HOME must be set}"
zdotdir=${ZDOTDIR:-$HOME}
xdg_config=${XDG_CONFIG_HOME:-$HOME/.config}
xdg_data=${XDG_DATA_HOME:-$HOME/.local/share}
xdg_state=${XDG_STATE_HOME:-$HOME/.local/state}
state_dir=${SHELL_CONFIG_STATE_DIR:-$xdg_state/shell-config-cutover}
snapshot=$state_dir/snapshot
active=$state_dir/active
restoring=$state_dir/restoring
legacy_base_ids='zshrc zprofile mise-config mise-lock starship atuin ai-config ai-bin ai-core ai-config-module ai-main ai-ui'
legacy_shell_ids='zshrc zprofile mise-config mise-lock starship atuin'
base_ids="$legacy_base_ids prompt-bin prompt-editor-bin prompt-context prompt-model prompt-server prompt-editor"
shell_ids="$legacy_shell_ids prompt-bin prompt-editor-bin prompt-context prompt-model prompt-server prompt-editor"
cutover_scope=all
layout_version=4
ids="$base_ids saved-path"

for value in "$repo_root" "$HOME" "$zdotdir" "$xdg_config" "$xdg_data" "$state_dir"; do
    reject_multiline "$value" path
done

target_for() {
    case "$1" in
        zshrc) printf '%s\n' "$target_zdot/.zshrc" ;;
        zprofile) printf '%s\n' "$target_zdot/.zprofile" ;;
        saved-path) printf '%s\n' "$target_zdot/.zshpath" ;;
        mise-config) printf '%s\n' "$target_config/mise/config.toml" ;;
        mise-lock) printf '%s\n' "$target_config/mise/mise.lock" ;;
        starship) printf '%s\n' "$target_config/starship.toml" ;;
        atuin) printf '%s\n' "$target_config/atuin/config.toml" ;;
        ai-config) printf '%s\n' "$target_config/ai/config.json" ;;
        ai-bin) printf '%s\n' "$target_home/.local/bin/ai" ;;
        ai-core) printf '%s\n' "$target_data/shell-config/ai/core.mjs" ;;
        ai-config-module) printf '%s\n' "$target_data/shell-config/ai/config.mjs" ;;
        ai-main) printf '%s\n' "$target_data/shell-config/ai/main.mjs" ;;
        ai-ui) printf '%s\n' "$target_data/shell-config/ai/ui.mjs" ;;
        prompt-bin) printf '%s\n' "$target_home/.local/bin/shell-prompt" ;;
        prompt-editor-bin) printf '%s\n' "$target_home/.local/bin/prompt-editor" ;;
        prompt-context) printf '%s\n' "$target_data/shell-config/prompt/context.mjs" ;;
        prompt-model) printf '%s\n' "$target_data/shell-config/prompt/model.mjs" ;;
        prompt-server) printf '%s\n' "$target_data/shell-config/prompt/server.mjs" ;;
        prompt-editor) printf '%s\n' "$target_data/shell-config/prompt/editor.html" ;;
        *) fail "unknown managed target id: $1" ;;
    esac
}

source_for() {
    case "$1" in
        zshrc) printf '%s\n' "$source_root/.zshrc" ;;
        zprofile) printf '%s\n' "$source_root/.zprofile" ;;
        saved-path) printf '%s\n' "$snapshot/path" ;;
        mise-config) printf '%s\n' "$source_root/config/mise/config.toml" ;;
        mise-lock) printf '%s\n' "$source_root/config/mise/mise.lock" ;;
        starship) printf '%s\n' "$source_root/config/starship.toml" ;;
        atuin) printf '%s\n' "$source_root/config/atuin/config.toml" ;;
        ai-config) printf '%s\n' "$source_root/config/ai/config.json" ;;
        ai-bin) printf '%s\n' "$source_root/bin/ai" ;;
        ai-core) printf '%s\n' "$source_root/ai/core.mjs" ;;
        ai-config-module) printf '%s\n' "$source_root/ai/config.mjs" ;;
        ai-main) printf '%s\n' "$source_root/ai/main.mjs" ;;
        ai-ui) printf '%s\n' "$source_root/ai/ui.mjs" ;;
        prompt-bin) printf '%s\n' "$source_root/bin/shell-prompt" ;;
        prompt-editor-bin) printf '%s\n' "$source_root/bin/prompt-editor" ;;
        prompt-context) printf '%s\n' "$source_root/prompt/context.mjs" ;;
        prompt-model) printf '%s\n' "$source_root/prompt/model.mjs" ;;
        prompt-server) printf '%s\n' "$source_root/prompt/server.mjs" ;;
        prompt-editor) printf '%s\n' "$source_root/prompt/editor.html" ;;
        *) fail "unknown managed source id: $1" ;;
    esac
}

use_current_paths() {
    source_root=$repo_root
    target_home=$HOME
    target_zdot=$zdotdir
    target_config=$xdg_config
    target_data=$xdg_data
}

load_snapshot_metadata() {
    [ -d "$snapshot" ] || fail "no cutover snapshot exists"
    layout_version=$(cat "$snapshot/version" 2>/dev/null || true)
    case "$layout_version" in
        1) cutover_scope=all; ids=$legacy_base_ids ;;
        2)
            cutover_scope=$(cat "$snapshot/scope")
            case "$cutover_scope" in
                all) ids="$legacy_base_ids saved-path" ;;
                shell) ids="$legacy_shell_ids saved-path" ;;
                *) fail "unsupported snapshot scope: $cutover_scope" ;;
            esac
            ;;
        3|4)
            cutover_scope=$(cat "$snapshot/scope")
            case "$cutover_scope" in all|shell) : ;; *) fail "unsupported snapshot scope: $cutover_scope" ;; esac
            ids=$(cat "$snapshot/ids")
            [ -n "$ids" ] || fail "snapshot has no managed ids"
            ;;
        *) fail "unsupported or incomplete snapshot" ;;
    esac
    snap_repo=$(cat "$snapshot/repo-root")
    snap_home=$(cat "$snapshot/home")
    snap_zdot=$(cat "$snapshot/zdotdir")
    snap_config=$(cat "$snapshot/xdg-config")
    snap_data=$(cat "$snapshot/xdg-data")
    for value in "$snap_repo" "$snap_home" "$snap_zdot" "$snap_config" "$snap_data"; do
        reject_multiline "$value" 'snapshot path'
    done
}

use_snapshot_paths() {
    source_root=$snap_repo
    target_home=$snap_home
    target_zdot=$snap_zdot
    target_config=$snap_config
    target_data=$snap_data
}

validate_sources() {
    use_current_paths
    for id in $ids; do
        target=$(target_for "$id")
        if [ "$id" != saved-path ] && ! { [ "$layout_version" = 4 ] && [ "$id" = mise-lock ]; }; then
            source=$(source_for "$id")
            [ -f "$source" ] || fail "missing repository source: $source"
            [ "$source" != "$target" ] || fail "repository source is also its managed target: $target"
        fi
        if [ -e "$target" ] && [ ! -f "$target" ] && [ ! -L "$target" ]; then
            fail "managed target has an unsupported file type: $target"
        fi
    done
}

install_pinned_tools() {
    mode=$1
    command -v mise >/dev/null 2>&1 || fail "mise is missing; install mise 2026.9.1 or newer first"
    (
        cd "$repo_root"
        unset MISE_CONFIG_FILE MISE_ENV
        MISE_GLOBAL_CONFIG_FILE="$repo_root/config/mise/config.toml"
        export MISE_GLOBAL_CONFIG_FILE
        case "$mode" in
            apply) mise install ;;
            preview) mise install --dry-run ;;
            *) fail "unknown provisioning mode: $mode" ;;
        esac
    )
}

plan() {
    if [ -e "$active" ]; then
        status
        printf 'shell-config: no migration changes are needed\n'
        return 0
    fi

    validate_sources
    reject_multiline "$PATH" PATH
    printf 'shell-config: would provision pinned tools from %s\n' "$repo_root/config/mise/config.toml"
    install_pinned_tools preview
    use_current_paths
    for id in $ids; do
        if [ "$layout_version" = 4 ] && [ "$id" = mise-lock ]; then
            printf 'shell-config: would remove obsolete lockfile %s\n' "$(target_for "$id")"
        else
            printf 'shell-config: would link %s -> %s\n' "$(target_for "$id")" "$(source_for "$id")"
        fi
    done
    printf 'shell-config: would retain the prior files in %s\n' "$snapshot"
    printf 'shell-config: would preserve the invoking shell PATH in the local snapshot\n'
}

migrate() {
    if [ -e "$active" ]; then
        activate
        return 0
    fi

    validate_sources
    printf 'shell-config: provisioning pinned tools\n'
    install_pinned_tools apply
    activate
}

snapshot_current() {
    next=$state_dir/snapshot.next.$$
    rm -rf "$next"
    mkdir -p "$next/items"
    printf '%s\n' "$layout_version" > "$next/version"
    printf '%s\n' "$cutover_scope" > "$next/scope"
    printf '%s\n' "$ids" > "$next/ids"
    # This is machine-local data, never executable shell code or a repo file.
    (umask 077; printf '%s\n' "$PATH" > "$next/path")
    printf '%s\n' "$repo_root" > "$next/repo-root"
    printf '%s\n' "$HOME" > "$next/home"
    printf '%s\n' "$zdotdir" > "$next/zdotdir"
    printf '%s\n' "$xdg_config" > "$next/xdg-config"
    printf '%s\n' "$xdg_data" > "$next/xdg-data"
    use_current_paths
    for id in $ids; do
        target=$(target_for "$id")
        mkdir -p "$next/items/$id"
        if [ -e "$target" ] || [ -L "$target" ]; then
            copy_preserve "$target" "$next/items/$id/value"
        else
            : > "$next/items/$id/absent"
        fi
    done
    printf '%s\n' "$next"
}

restore_one_from() {
    from_snapshot=$1
    id=$2
    target=$3
    mkdir -p "$(dirname -- "$target")"
    rm -f "$target"
    if [ -e "$from_snapshot/items/$id/value" ] || [ -L "$from_snapshot/items/$id/value" ]; then
        copy_preserve "$from_snapshot/items/$id/value" "$target"
    fi
}

rollback_current_from() {
    from_snapshot=$1
    use_current_paths
    for id in $ids; do
        restore_one_from "$from_snapshot" "$id" "$(target_for "$id")"
    done
}

rollback_failed_activation() {
    exit_code=$?
    trap - HUP INT TERM EXIT
    if [ "${cleanup_next:-0}" -eq 1 ]; then
        rollback_current_from "$next" || true
        rm -rf "$next"
    fi
    exit "$exit_code"
}

activate() {
    if [ -e "$active" ]; then
        load_snapshot_metadata
        [ "$snap_repo" = "$repo_root" ] || fail "another checkout is active: $snap_repo; restore it first"
        status >/dev/null || fail "active installation has drifted; restore or repair it before reactivating"
        printf 'shell-config: already active from %s\n' "$repo_root"
        return 0
    fi

    validate_sources
    reject_multiline "$PATH" PATH
    mkdir -p "$state_dir"
    next=$(snapshot_current)
    cleanup_next=1
    trap rollback_failed_activation HUP INT TERM EXIT

    use_current_paths
    for id in $ids; do
        target=$(target_for "$id")
        mkdir -p "$(dirname -- "$target")"
        rm -f "$target"
        if ! { [ "$layout_version" = 4 ] && [ "$id" = mise-lock ]; }; then
            source=$(source_for "$id")
            ln -s "$source" "$target"
        fi
    done

    rm -rf "$snapshot"
    mv "$next" "$snapshot"
    : > "$active"
    cleanup_next=0
    trap - HUP INT TERM EXIT
    printf 'shell-config: activated %s\n' "$repo_root"
    printf 'shell-config: open a fresh terminal; restore with %s restore\n' "$0"
}

prevalidate_restore() {
    load_snapshot_metadata
    [ "$snap_repo" = "$repo_root" ] || fail "this checkout did not create the active snapshot: $snap_repo"
    use_snapshot_paths
    for id in $ids; do
        target=$(target_for "$id")
        if [ "$layout_version" = 4 ] && [ "$id" = mise-lock ]; then
            [ ! -e "$target" ] && [ ! -L "$target" ] || fail "managed target changed since activation: $target"
        else
            source=$(source_for "$id")
            [ -L "$target" ] || fail "managed target changed since activation: $target"
            [ "$(readlink "$target")" = "$source" ] || fail "managed symlink changed since activation: $target"
        fi
    done
}

restore() {
    [ -e "$active" ] || { printf 'shell-config: already restored\n'; return 0; }
    if [ ! -e "$restoring" ]; then
        prevalidate_restore
        : > "$restoring"
    else
        load_snapshot_metadata
    fi
    use_snapshot_paths
    for id in $ids; do
        restore_one_from "$snapshot" "$id" "$(target_for "$id")"
    done
    rm -f "$active" "$restoring"
    printf 'shell-config: restored the pre-activation configuration\n'
    printf 'shell-config: open a fresh terminal to complete the cutover\n'
}

status() {
    if [ ! -e "$active" ]; then
        if [ -d "$snapshot" ]; then
            printf 'shell-config: inactive; previous snapshot retained at %s\n' "$snapshot"
        else
            printf 'shell-config: inactive; no snapshot yet\n'
        fi
        return 0
    fi
    load_snapshot_metadata
    use_snapshot_paths
    drift=0
    for id in $ids; do
        target=$(target_for "$id")
        if [ "$layout_version" = 4 ] && [ "$id" = mise-lock ]; then
            changed=0
            [ ! -e "$target" ] && [ ! -L "$target" ] || changed=1
        else
            source=$(source_for "$id")
            changed=0
            [ -L "$target" ] && [ "$(readlink "$target" 2>/dev/null || true)" = "$source" ] || changed=1
        fi
        if [ "$changed" -ne 0 ]; then
            printf 'shell-config: drift: %s\n' "$target" >&2
            drift=1
        fi
    done
    [ "$drift" -eq 0 ] || return 1
    printf 'shell-config: active from %s\n' "$snap_repo"
}

command=${1:-status}
if [ "$#" -eq 2 ] && [ "$2" = --shell-only ]; then
    case "$command" in
        plan|migrate|activate) cutover_scope=shell; ids="$shell_ids saved-path"; set -- "$1" ;;
        *) fail "--shell-only requires plan, migrate, or activate" ;;
    esac
fi
case "$command" in
    plan) [ "$#" -eq 1 ] || fail "plan takes no arguments"; plan ;;
    migrate) [ "$#" -eq 1 ] || fail "migrate takes no arguments"; migrate ;;
    activate) [ "$#" -eq 1 ] || fail "activate takes no arguments"; activate ;;
    restore) [ "$#" -eq 1 ] || fail "restore takes no arguments"; restore ;;
    toggle)
        [ "$#" -eq 1 ] || fail "toggle takes no arguments"
        if [ -e "$active" ]; then restore; else activate; fi
        ;;
    status) [ "$#" -eq 1 ] || fail "status takes no arguments"; status ;;
    -h|--help|help) usage ;;
    *) usage >&2; fail "unknown command: $command" ;;
esac

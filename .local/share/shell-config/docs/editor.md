# Neovim and Yazi

Neovim uses a small Lua setup with FFF file/content search, WhichKey shortcut
help, Git change markers, and native language-server completion. Press Space
and pause to discover shortcuts, or press Space then `?` to open the full map.

| Keys | Action |
|---|---|
| `Space f f` | Find files with FFF |
| `Space f g` | Search file contents with FFF |
| `Space c f` | Format the current buffer using its one attached formatter |
| `Space c a` / `Space c r` | Code actions / rename symbol |
| `Space c d` | Show line diagnostics |
| `Space g d` / `Space g r` | Definition / references |
| `Space g s` / `Space g D` | Preview Git change / diff against the index |
| `[h` / `]h` | Previous / next Git change |
| `Ctrl-h/j/k/l` | Move between splits |
| `Alt-h/j/k/l` | Move a split |
| `Ctrl-Space` in insert mode | Request language-server completion |
| `Ctrl-n` / `Ctrl-p`, then `Ctrl-y` | Select and accept a completion |
| `Space h t` | Neovim's interactive tutor |
| `Space h c` | Check editor dependencies |

Neovim also provides `K` for symbol hover, `gc` for commenting, `[d` / `]d` for
diagnostics, native snippets, and `:help`. FFF's picker uses Enter to open,
Esc to close, Ctrl-n/p to move, Ctrl-v for a vertical split, and Ctrl-d/u to
scroll the preview.

Language servers start only when their executable exists on PATH:

| Language | Server command | Responsibility |
|---|---|---|
| Python | `ty server` | Types, navigation, completion |
| Python | `ruff server` | Linting, formatting, import actions |
| JS/TS, JSON, CSS, GraphQL | `biome lsp-proxy` | Linting, formatting, actions in a Biome project |
| Kotlin | `kotlin-lsp --stdio` | Kotlin language services in a Gradle/Maven project |

Formatting runs when requested, preserves unsaved edits, and does not save the
file. If no attached server supports formatting, or more than one does,
Neovim reports the condition and leaves the buffer alone. Completion triggers
on server-defined characters; Ctrl-Space requests it manually. For attachment
and diagnostics, inspect `:lsp` and `:checkhealth editor`.

Yazi keeps its native keymap. Launch it with the shell's `y` wrapper to return
to the selected directory on `q`; use `Q` to quit while preserving the shell
directory. Press `F1` or `~` for help; `Ctrl-C` closes the help menu. `s` searches
filenames with fd, `S` searches contents with ripgrep, `z` opens fzf navigation,
and `Z` opens zoxide
navigation. `?` finds the previous filename match. Open files with Enter and
choose an opener with `O`.

The required editor tools are Neovim 0.12+, Git, curl, fd, and ripgrep. Yazi,
fzf, and zoxide supply the file manager and navigation. The four language-server
commands are optional. FFF downloads the native backend for its pinned stable
release; building it from source requires Rust tooling if the prebuilt download
is unavailable. A Nerd Font is optional; shortcut labels use plain text.

Plugin commits are pinned in `config/nvim/lua/editor/plugins.lua`. Run
`nvim --headless '+Lazy! sync' +qa` after installation, then check
`:FFFHealth`, `:checkhealth which-key`, and `:checkhealth editor`.
The generated Lazy lock file belongs to Neovim's state directory. Keep the
legacy `init.vim` and `.vimrc` in the installer's backup; only one Neovim init
file should occupy the active configuration directory.

The focused configuration check is `python3 tests/editor.py`. It uses temporary
HOME/XDG directories and does not download plugins or modify the installed
editor. Native plugin and language-server integration requires a separately
provisioned disposable editor profile.

Sources: [FFF](https://github.com/dmtrKovalenko/fff#fffnvim),
[WhichKey](https://github.com/folke/which-key.nvim),
[Neovim LSP](https://neovim.io/doc/user/lsp.html),
[ty](https://docs.astral.sh/ty/editors/#neovim),
[Ruff](https://docs.astral.sh/ruff/editors/setup/#neovim),
[Biome](https://biomejs.dev/editors/third-party-extensions/),
[Yazi](https://yazi-rs.github.io/docs/quick-start/),
[Yazi keymap](https://github.com/sxyazi/yazi/blob/v26.9.1/yazi-config/preset/keymap-default.toml#L332-L337).

# Everyday terminal tools

Open a fresh terminal after installing the files. The editor defaults to Neovim;
an explicitly set `EDITOR`/`VISUAL` or `.zshrc.local` can choose another editor.

| Command | Purpose |
| --- | --- |
| `nvim` | Files, FFF filename/content search, WhichKey, native LSP, Git changes |
| `y [path]` | Yazi with directory handoff: `q` changes the shell directory; `Q` keeps it |
| `keys [shell\|nvim\|yazi\|work]` | Search useful shortcuts and examples |
| `keys shell --list` | Print a cheat sheet without a terminal UI |
| `p [search]` | Select a project/worktree and enter its directory |
| `project --list` | Print discovered project/worktree paths |
| `scratch [name]` | Open a persistent private Markdown note |
| `scratch --temp` | Open a disposable note removed after the editor exits |
| `work` | GitHub/Jira queue and review actions |
| `review PR_URL` | Open a PR in an owned detached review worktree |
| `config shell add "request"` | Pi-assisted reusable helper |
| `config shell completion COMMAND "request"` | Create or update that command's Zsh completion |
| `config shell change "request"` | Pi-assisted public configuration change |

At the prompt, `Ctrl-X`, then `?` opens `keys`. Neovim's `Space` prefix shows
WhichKey; `Space ?` opens it immediately. Yazi's full help uses `F1` or `~`.
The shell cheat sheet reads `# description:` and `# usage:` headers from
installed helper scripts/functions. Neovim help reads actual described mappings
from your current configuration. Work panel help shares its action definitions.
Yazi entries are a small curated list of upstream defaults; native help is the
authority for every mapping.

Keep custom Zsh completions together in `~/.config/zsh/completions/`, one
`_command` file per command, beginning with `#compdef command`. The directory
already precedes packaged completions in `fpath`; `compinit` rescans handler
registrations on shell startup. Add files directly with your editor or use `config shell completion`
to let Pi maintain a handler and commit it. Run `zs` to load new registrations;
manual additions enter Git with `config add -f -- .config/zsh/completions/_command`.

`fd` provides fzf's file/directory candidates, respecting ignore rules and
excluding `.git`. Atuin owns `Ctrl-R`, fzf owns `Ctrl-T`/`Alt-C`, and the prompt
does no GitHub/Jira requests. `bat` is available for file previews and direct use.

Projects are discovered one level below `$HOME/code`; Git supplies their
registered worktrees. Set `PROJECT_ROOTS` to a colon-separated list of explicit
directories in `.zshrc.local` to add roots. Paths are data, never shell code.

Scratch notes live in `~/.local/share/shell-config/scratch` with private file
permissions and stay outside the public Git inventory. Tool credentials,
sessions, generated editor/plugin data, and account metadata remain private.
See [editor](editor.md), [work](work.md), and [config shell](config-shell.md).

Focused checks:

```sh
python3 tests/helpers.py
python3 tests/editor.py
python3 tests/work.py
python3 tests/configure.py
```

# Bare Git dotfiles

The migration uses [Atlassian's dotfiles pattern](https://www.atlassian.com/git/tutorials/dotfiles):
Git stores its metadata in `~/.cfg`, while `$HOME` is the working tree. Public
configuration becomes ordinary files at its live destinations. The original
checkout remains available, but the HOME working tree becomes the configuration
authority after migration.

## Migrate this checkout

Run from this checkout in the shell whose PATH you want to retain. Git and
Python 3 are required; `fzf` and a TUI package are not required.

```sh
./bin/dotfiles plan
./bin/dotfiles
```

The default command opens a migration wizard when migration is inactive. It
shows the planned destinations and asks before applying them. For an explicit
noninteractive migration:

```sh
./bin/dotfiles migrate --yes
```

Migration snapshots every managed destination before replacing it, including
files, symlinks, permissions, and destinations that were absent. It builds a
HOME-shaped commit whose parent is the source checkout's HEAD and retains the
full history and `origin` in the bare repository. Current public source content
is included, so the conversion also retains reviewed local configuration edits.
The conversion commit is local and unpushed. No tool installation, package
update, or Pi settings merge runs during migration.

An active legacy `install.sh` cutover can be transferred directly. The migration
transfers the active marker and keeps its old snapshot so that restoring the
bare migration restores the legacy cutover too. The source checkout is kept.

Open a fresh terminal after migration. Existing installed tools remain
available. If the pinned tools need installation, ensure mise itself is installed
and run `mise install` from a directory outside any project; inspect
`mise config ls` first. Tool provisioning is a separate operation.

## Managed paths

The migration uses a fixed public allowlist. It never discovers files by scanning
HOME or copies all of an application's local state.

| Source | HOME destination |
| --- | --- |
| `.zshrc`, `.zprofile` | `.zshrc`, `.zprofile` |
| `config/mise`, `config/atuin`, `config/ai` | `.config/mise`, `.config/atuin`, `.config/ai` |
| `config/starship.toml`, `config/prompt.json` | `.config/starship.toml`, `.config/prompt.json` |
| Public command wrappers under `bin/` | `.local/bin/` |
| AI and prompt modules under `ai/` and `prompt/` | `.local/share/shell-config/ai/`, `.local/share/shell-config/prompt/` |
| Migration driver, documentation, public Pi preferences and merger | `.local/share/shell-config/` |
| `pi/pi-lsp.json`, `pi/agents/`, `pi/prompts/` | `.pi/agent/pi-lsp.json`, `.pi/agent/agents/`, `.pi/agent/prompts/` |

The plan command is the exact file inventory. Generated `config` and `dotfiles`
wrappers join the managed command wrappers. `.zshpath` becomes local plain PATH
data: existing data is retained, or the invoking PATH is captured when needed.
It stays ignored. Private snapshots stay outside the public repository.

`.zshrc.local`, `.zshpath`, Pi `settings.json` and authentication, agent sessions,
shell history, credentials, and unrelated HOME files are not tracked. The root
`.gitignore` denies new paths by default, and Git hides untracked HOME files with
`status.showUntrackedFiles=no`. These choices prevent accidental whole-HOME
staging; they do not replace reviewing a diff before committing.

Migration requires the default HOME-relative roots. It refuses custom
`ZDOTDIR`, XDG config/data/state roots, or Pi roots outside the defaults,
symlinked destination ancestors, and an existing `~/.cfg` that it does not own.
Resolve the reported conflict before retrying; migration does not silently
adopt an unrelated repository or redirect writes through a symlinked directory.

## Daily use

After opening a fresh terminal:

```sh
dotfiles
```

The menu offers status, diffs, a numbered chooser for tracked files to stage,
a commit-message prompt, and migration or restore actions. It never stages
everything automatically or pushes. The installed command is
`~/.local/bin/dotfiles`; Python 3 is needed for this menu and migration/restore.

`~/.local/bin/config` is a Git wrapper with `--git-dir="$HOME/.cfg"` and
`--work-tree="$HOME"`. It needs Git, and accepts ordinary Git commands:

```sh
config status
config diff
config add -- .zshrc .config/starship.toml
config diff --cached
config commit
config push -u origin HEAD
```

New HOME paths are ignored until explicitly admitted. To add an existing vimrc
after reviewing it for private content:

```sh
config add -f -- .vimrc
config diff --cached -- .vimrc
config commit
```

Once a path is tracked, normal `config add -- <path>` works. Use HOME-relative
paths in these examples; the wrapper resolves them from HOME in every directory.
Edits in the old source checkout no longer
update the live files.

## Public Pi preferences

Migration installs the public LSP configuration, profiles, and optional prompt.
It keeps `~/.pi/agent/settings.json` and authentication unchanged. Preview and
then explicitly merge the managed preferences and package references with:

```sh
python3 "$HOME/.local/share/shell-config/pi/sync-settings.py"
python3 "$HOME/.local/share/shell-config/pi/sync-settings.py" --apply
```

The merger preserves unrelated settings and package choices; see
[the Pi guide](pi.md) for its contract and separate extension installation.
The checkout-based link commands in that guide are for the legacy installation;
the bare migration already installs the managed public files at their destinations.

## Restore

Use the menu's restore action, or run:

```sh
dotfiles restore --yes
```

Restore first verifies the migrated files, Git HEAD, and index against the
recorded migration state. If any have changed, it refuses before overwriting
them. Review and preserve the reported work before resolving drift; restore
does not discard later edits or commits to force rollback.

A successful restore reinstates the exact pre-migration files, symlinks,
permissions, and absence, then moves `~/.cfg` into the private backup. Open a
fresh terminal afterward. The source checkout and legacy installer remain
available; if the old checkout cutover was active, its original snapshot is
still available to `./install.sh restore`.

The private state directory defaults to
`~/.local/state/shell-config-dotfiles`. `DOTFILES_STATE_DIR` can select a separate
backup directory; it must not overlap the source checkout, bare repository, or
managed files. The snapshot retains every preimage and an independent Git
repository for checking the complete imported inventory. Do not remove it while
rollback is needed. A completed restore retains that snapshot; choose a new
empty backup directory for another migration.

Failures emit bounded `stage=... outcome=...` diagnostics. An interrupted
migration or restore retains its recovery authority; run the restore command
from the preserved checkout if the installed wrapper is not yet available.
A directory lock prevents overlapping operations. Normal cancellation, SIGINT,
SIGTERM, and SIGHUP release it. A force-killed process can leave the lock behind;
verify that no migration process is running before removing that empty lock.

## Verification

Run the isolated migration checks from this checkout:

```sh
python3 tests/dotfiles.py
```

The migration also tracks these checks in the HOME worktree:

```sh
python3 "$HOME/.local/share/shell-config/tests/dotfiles.py"
```

They exercise temporary homes with spaces, full history and origin retention,
private-state exclusions, source and destination symlink rejection, exact
restoration, drift and corrupted-snapshot rejection, interrupted operations,
repository conflicts, and real terminal confirmation, selective staging, and
local commits. Legacy shell, AI, and Pi checks remain in the preserved checkout.

## Set up another machine

First explicitly commit and push the converted branch from the migrated
machine. The source branch on the remote has the old layout until that push.
Replace the placeholders below with the published branch and repository URL:

```sh
git clone --bare --branch <branch> <repository-url> "$HOME/.cfg"
git --git-dir="$HOME/.cfg" --work-tree="$HOME" config status.showUntrackedFiles no
git --git-dir="$HOME/.cfg" --work-tree="$HOME" checkout
```

Back up conflicting destination files before checkout; inspect Git's reported
conflicts instead of forcing an overwrite. The local migration backup is not
part of the published repository, so this clone does not bring the first
machine's restore point with it. Open a fresh terminal, provision the pinned
tools separately with mise, and follow the Pi guide for extension installation.

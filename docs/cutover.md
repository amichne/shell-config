# Reversible shell-config cutover

Use the top-level installer when evaluating this repository against an existing
shell setup. It is intentionally a cutover mechanism, not a package manager: the
repository remains the configuration authority while active, and the prior
machine state remains independently recoverable.

```sh
./install.sh status
./install.sh activate
# Open a fresh terminal and evaluate the repository configuration.
./install.sh restore
# Open a fresh terminal and you are back on the previous configuration.
```

`./install.sh toggle` performs whichever transition is needed. `activate` and
`restore` are idempotent when already in their requested state.

## Invariant

Before activation, the installer snapshots every path it owns, including whether
a path was absent or was itself a symlink. It then replaces only those leaf paths
with symlinks into the current checkout. `restore` first proves that every managed
path is still the symlink installed by this checkout; if any target drifted, it
fails before overwriting anything. A successful restore recreates the snapshot
and leaves it available for inspection.

A new activation always replaces the previous snapshot with the configuration
that exists immediately before that activation. This makes repeated
`toggle`/evaluation cycles behave naturally: changes made to the old setup while
it is restored become the next rollback point.

The installer manages:

- `${ZDOTDIR:-$HOME}/.zshrc` and `.zprofile`;
- the repository's mise, Starship, Atuin, and AI configuration under
  `${XDG_CONFIG_HOME:-$HOME/.config}`;
- `~/.local/bin/ai`;
- the four AI runtime modules under
  `${XDG_DATA_HOME:-$HOME/.local/share}/shell-config/ai`.

It deliberately does **not** manage `.zshrc.local`, application credentials,
Vim/Git/IDE configuration, shell history databases, or any existing agent login
state. Machine-specific paths therefore remain outside the cutover.

## State and recovery

State lives at:

```text
${SHELL_CONFIG_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/shell-config-cutover}
```

The snapshot records the exact HOME, ZDOTDIR, XDG config/data roots, and checkout
path used for that activation. Restore therefore does not depend on the current
shell inheriting the same XDG environment.

Do not delete this state directory while the repository is active. If a managed
file is intentionally replaced while active, `restore` will refuse because doing
otherwise would silently discard that change. Put the expected repository
symlink back, then restore. The `status` command identifies drifted targets.

The snapshot uses the platform `cp -a` implementation when available, with a
portable recursive/preserve fallback. It preserves ordinary file contents,
permissions, timestamps, directories, and symlinks; this is not an archival
contract for every filesystem-specific ACL or extended attribute.

The script does not install mise, download locked tools, authenticate AI
harnesses, or start a new shell. Provision prerequisites first. A new terminal is
required after either shell cutover because the currently running process has
already evaluated its startup files and environment.

## Why symlinks for cutover

The permanent migration instructions can still deploy plain copied files.
Evaluation needs a different property: editing the checkout should immediately
change the active configuration without repeatedly reinstalling files. Symlinks
establish that ownership explicitly and make `status` mechanically provable.
The snapshot supplies the inverse operation.

## Verification

The cutover test runs entirely in temporary HOME/ZDOTDIR/XDG directories:

```sh
python3 tests/install-cutover.py
```

It covers paths containing spaces, ZDOTDIR, existing regular files, existing
symlinks, absent files, file modes, activation, restoration, repeated toggles,
and fail-closed drift detection. It never reads or modifies the real home.

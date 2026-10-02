# Pi-assisted shell configuration

`config shell` opens a dedicated interactive Pi session with the current public
shell architecture and a private candidate workspace. When you finish a valid
proposal and exit Pi successfully, the command validates it, installs the
selected changes, and creates a local commit in `~/.cfg` with HOME as its
worktree. It never pushes.

```sh
config shell add
config shell add "Create a helper that finds the current Git root"
config shell add --name project-root --kind function "Change to the Git root"
config shell edit project-root
config shell change "Hide the blank prompt line" --paths .config/starship.toml
config shell change "Adjust my shell and prompt configuration"
config shell completion port-owner "Suggest --help and common TCP ports"
config shell completion future-tool
```

Use a command in `~/.local/bin` for an independent process. Use an autoloaded
function in `~/.config/zsh/functions` for operations such as `cd` or `export`
that must affect the invoking shell. Helpers include `# description:` and
`# usage:` comments so the shell's help can discover them. Open a fresh shell
with `zs` after adding a function.

`edit NAME` resolves one existing tracked helper. `change` without `--paths`
selects the tracked public configuration and documentation inventory. It
refuses preexisting edits to any selected path; use `--paths` to select a smaller
set when unrelated configuration has local changes. An explicit path may also
name a new public configuration file. Each candidate remains bounded by the
selected paths, even if you ask Pi to expand the request during the session.
Start another session to expand that scope.

Keep all custom Zsh completions in `~/.config/zsh/completions/`. Each ordinary
`_COMMAND` file begins with `#compdef COMMAND`; the directory is already on
`fpath` before `compinit` and takes precedence over system completion files.
You can edit these files directly and run `zs` to load them in a fresh shell.
There is no separate registry to update.

For example, create `~/.config/zsh/completions/_my-tool` with:

```zsh
#compdef my-tool
_arguments \
  '--help[Show usage]' \
  '--verbose[Show detailed output]'
```

Run `zs`, then type `my-tool --` and press Tab.

`config shell completion COMMAND [REQUEST...]` is the optional Pi creation and
maintenance flow for that same location. It permits declarative registrations
for installed CLIs, aliases, shell functions, project tools, and future commands.
Names support digits, uppercase letters, underscores, dots, plus signs, and
hyphens. A tracked handler can be edited; an existing untracked handler is
preserved and rejected until you explicitly review and track it.

Completion proposals require the exact `#compdef` registration and bounded
`COMPLETION_SMOKE` checks. A check supplies completed argument words, the current
word prefix, and expected or excluded candidates. The finisher loads real
`compinit`, invokes a real ZLE completion widget, and captures native matches
for that command and input. It never executes the typed command line. This
proves the asserted handler behavior in a disposable HOME without a project,
personal history, or user completion styles; it does not prove every dynamic
completion against every local project.

Allowed configuration includes `.zshrc`, `.zprofile`, public Starship/AI/prompt
files, and supported text configuration under `.config/mise`, `atuin`, `nvim`,
`yazi`, `work`, `worktrunk`, and `ghostty`. Documentation stays under
`.local/share/shell-config/docs`. New helpers use the two directories above.
Authentication, credentials, histories, caches, sessions, state, private/local
files, Pi account data, runtime implementation files, and protected commands
such as `config` are excluded. A public path is an authorization boundary, not
proof that arbitrary file contents contain no secrets.

Completion handler code has its own public namespace: a target named `auth-tool`
or `foo.local` does not turn its `_COMMAND` handler into a credential file.
Parent-directory, symlink, scope, and ownership checks still apply.

Pi runs with automatic extensions, skills, prompt templates, and context-file
discovery disabled. Its maintained instructions and the selected architecture
copies are supplied explicitly. HOME is never enumerated for context or
staging. The private workspace is scoped context, not an OS permissions sandbox;
Pi and helper smoke tests use the account's normal permissions.

Each session snapshots its selected live preimages, Git HEAD and branch, and
real index. Pi writes candidates and an exact `CHANGESET` proposal. A successful
process exit alone cannot commit. The finisher checks the recorded preconditions
again, rejects symlinks and special permissions, validates supported syntax,
and runs bounded helper behavior checks in a disposable HOME. It proves Zsh
function autoload with the real shell. ShellCheck applies to sh/bash scripts;
Zsh uses its own syntax parser. Python, TOML, JSON, Lua, and Ghostty use their
corresponding compiler or parser. A missing required validator fails closed.

The commit uses a temporary index containing only candidate changes. The real
index is separately prepared with exactly those entries updated, so unrelated
staged edits remain staged. Git's index lock guards installation and publication.
Commit construction uses Git plumbing and the typed validated changeset rather
than commit hooks. `commit.gpgsign` is honored; a signing failure rolls back.
Content is committed directly without clean filters. HEAD, branch,
or index drift rejects the session. A commit failure restores exact file
preimages and original absence; unknown competing changes produce
`RECOVERY_REQUIRED` and preserve the private evidence.

Failures print bounded `stage=... outcome=...` diagnostics without source or
tool output. Session IDs are printed when Pi starts. Successful Pi sessions that
fail validation retain their candidate files for correction and retry:

```sh
config shell finish SESSION_ID
```

`finish` requires the recorded successful Pi exit, original repository
preconditions, and a valid explicit proposal. Failed or still-running Pi
sessions cannot finish. Repeating a completed session reports its existing
commit. Private preimages and receipts remain under
`~/.local/state/shell-config/sessions` and `~/.cfg/config-shell-authority`.
The old checkout is never an installation or commit destination.

The wrapper holds an advisory session lock while Pi runs. Lifecycle receipts
assume the wrapper's private state remains unmodified; they do not attest to
an adversarial process with the same account permissions. Rollback verifies
saved preimages and restored content. Unknown reference movement or uncertain
index publication requires recovery and cannot report a successful restoration.

Run the isolated checks without a model call or live HOME mutation:

```sh
python3 tests/configure.py
```

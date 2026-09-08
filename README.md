# A smaller shell setup

This is a reviewable replacement for the active Apollo/Artemis startup and the
unused custom dotfiles runtime. It has not been applied to the live home.
The existing repositories and their uncommitted changes are untouched.

Use Zsh, mise for tool versions, and Starship for the prompt. Keep Atuin for
history and zoxide plus Worktrunk for navigation. Deploy plain files with the
standard `install` command. There is no management CLI, feature registry,
profile parser, plugin loader, chezmoi adapter, or checkout path in startup.

## What your history supports

The read-only Atuin analysis covered 2,875 non-deleted records from
2026-06-07 through 2026-09-05 UTC. The most recent 30 days contained 885 records.
Only command names, selected public subcommand names, counts, and exit statuses
were reported. Arguments, working directories, hostnames, tokens, and raw
history were not copied into this directory.

| Leading command | Records | Implication |
| --- | ---: | --- |
| `kast` | 574 | Keep its installed CLI reachable through `~/.local/bin`. |
| `l` | 236 | Keep this small listing alias. |
| `codex` | 196 | Preserve the application and its own configuration. |
| `vim` | 107 | Keep Vim as the editor and preserve the existing vimrc. |
| `git` | 106 | Prefer native Git behavior and completion. |
| `cd` | 99 | Ordinary directory navigation remains central. |
| `brew` | 77 | Retain Homebrew for system packages and applications. |
| `idea` | 61 | Retain the machine's JetBrains launcher path. |
| `./gradlew` | 60 | Repository wrappers should own Gradle. |
| `kast-dev` | 53 | Previously used, currently unresolved on the inspected PATH. |
| `wt` | 52 | Preserve Worktrunk's shell integration, including directory changes. |
| `source` | 50 | Startup maintenance is visible in daily usage. |
| `main` | 43 | Provide a simpler default-branch navigation shortcut. |
| `gh` | 41 | Keep GitHub CLI; use its native authentication. |
| `zs` | 20 | Replace repeated sourcing with a fresh login shell. |
| `push` | 20 | Replace with native Git push configuration. |
| `commit` | 16 | Use explicit staging and native commit behavior. |
| `f` | 15 | Keep the target-before-pattern spelling, backed by ripgrep. |
| `sdk` | 12 | Move Java selection to mise; avoid a global Gradle install. |
| `z` | 12 | Keep zoxide; 11 uses occurred in the last 30 days. |

These are first-token counts, not a shell syntax tree or a complete activity
log. Commands later in pipelines, absolute executable paths, aliases, editor
builds, prompt hooks, and key presses are not counted as equivalent commands.
Absence from this table is not evidence that a tool is unnecessary. In
particular, Atuin searches and completion usage are not measured by history.

Every recorded `zs` invocation had a nonzero exit status. Of 50 `source`
invocations, 37 had a nonzero status, and 28 targeted `~/.zshrc`. These statuses
do not prove that every part of startup failed. They support making reload
behavior simpler and testing its result.

## Current sources of complexity

The live `.zshrc` loads both `apollo/zsh-entrypoint.sh` and the Artemis entrypoint.
It also initializes zoxide before Apollo initializes it again. Apollo loads
Oh My Zsh, overrides commands such as `git`, performs extension synchronization,
and loads SDKMAN. The live `.zprofile` selects SDKMAN's Java home directly.
GitHub tokens are fetched and exported by the live `.zshrc`.

The newer dotfiles repository adds its own schema, dependency registry, plugin
manifests, layered configuration, migration CLI, and optional chezmoi deployment.
It is not the runtime sourced by the live startup file. Its tracked shell files
total 3,727 lines across 42 files; Apollo has 12,436 across 61 files. Those counts
include tests and completions, so they are not startup execution costs.

The old code calls `wt` integration "Warp". The installed command is Worktrunk
0.53.0. History includes 30 `wt switch` and 12 `wt remove` commands. Removing its
shell integration would break automatic directory changes.

## Ownership and portability

| Concern | Owner |
| --- | --- |
| Login PATH | `.zprofile`, with mise shims |
| Interactive options, completions, four shortcuts | `.zshrc` |
| Tool versions and binary download locations | `config/mise/config.toml` and `mise.lock` |
| Prompt | `config/starship.toml` |
| History behavior | `config/atuin/config.toml` |
| Machine-specific launcher paths | Optional, untracked `.zshrc.local` |
| Java/Node versions for a project | That repository's `mise.toml` |
| Gradle distribution and tasks | That repository's `gradlew` and Gradle configuration |
| GUI apps, Git, Zsh, Vim, OS libraries | Homebrew or the Linux package manager |
| Authentication, identities, agent settings | Each application's existing local storage |

The four shortcuts are `l`, `zs`, `main`, and `f`; `rr` is a small directory
function. No standard executable is replaced. The only required shell
integrations are native initialization output from mise, zoxide, Worktrunk,
Atuin, and Starship.

Support targets are Zsh on macOS and glibc-based Linux, on ARM64 and x86-64.
The lockfile contains URLs and SHA-256 checksums for all 12 tools on all four
targets. macOS ARM64 installation and behavior were exercised locally. Other
targets have lockfile coverage, not runtime proof. Windows and Bash are outside
this draft. No Nerd Font is required by the prompt.

Self-contained means these files do not source Apollo, dotfiles, a Codex plugin,
or a repository at a fixed path. Fresh provisioning still needs internet access,
Zsh, Git, Vim, standard OS utilities, and mise. A fully offline machine also needs
the binaries and native libraries supplied separately; copying configuration
alone cannot provide those.

## Use Developer Tools without another runtime

The installed Developer Tools 1.0.0 plugin supplies skills, not a shell plugin
manager or an executable package catalog. Use its `mise-project-tooling`,
`cli-data-pipelines`, `shell-session-integration`, and `git-change-flow` workflows
to maintain these native files. Its `cli-creator` skill is useful for a real
domain CLI such as Kast; recreating a dotfiles management CLI would add the
complexity this migration removes.

| Job | Use |
| --- | --- |
| Search content | `rg -n -- 'pattern' path` |
| Find filenames | `fd --type f -- 'pattern' path` |
| Read structured JSON | `jq '.field' file.json` |
| Select interactively | `fd --type f --print0 \| fzf --read0 --print0` |
| Inspect tool selection | `mise ls`, `mise which java`, `mise doctor` |
| Run a reproducible build | `mise install --locked && mise exec -- ./gradlew test` |
| Switch worktrees | `wt switch`, `wt switch '^'`, `wt switch -` |
| Install a Python application temporarily | `uvx <package>` |

Keep `bat`, `yq`, `sd`, `delta`, and other specialist tools optional. The local
`yq` is Mike Farah's implementation, so use that identity when adding it with
mise. More installed tools do not require more shell startup code. Agent
automation should use bounded commands and structured output, without `fzf`.

## Deliberate behavior changes

- `l` runs standard `ls -lah`. `ls` itself is untouched; lsd's icons and grouping
  are not retained.
- `main` runs `wt switch '^'`. It navigates to the default-branch worktree and
  does not fetch or pull. Fetch explicitly when freshness is required.
- `zs` runs `exec zsh -l`. It starts a fresh shell; check for active jobs first.
  Already inherited credentials and PATH entries are not cleansed by `exec`.
  Fully reopen the terminal application for the first migration.
- `f PATH PATTERN` uses ripgrep, which respects ignore rules and normally skips
  hidden files. Use `rg --hidden` or `rg -uuu` explicitly when needed.
- `commit` and `push` are retired. Use `git add -- <paths>` followed by
  `git commit`, and `git push -u origin HEAD` for the first push. If desired,
  `git config --global push.autoSetupRemote true` makes ordinary `git push`
  establish upstreams under Git's supported push modes.
- Automatic ticket prefixes, staging every file, Git overrides, OMZ aliases,
  autosuggestions, highlighting, framework plugins, and SDKMAN hooks are absent.
  A repository that requires ticket enforcement should encode it there.
- Ctrl-R selects an Atuin result into the command line. Enter there executes
  it. Up-arrow stays with Zsh. This is a proposed preference, not a conclusion
  about key usage from history. Set `enter_accept = true` to retain instant
  execution from Atuin's result list.
- Atuin automatic sync and update checks are disabled. Run `atuin sync`
  explicitly when needed. History databases, encryption keys, and login state
  are not deployed or replaced.

## Try the setup with a reversible cutover

After installing mise 2026.9.1 or newer, preview every managed path and the
locked tool installation, then migrate in one command:

```sh
./install.sh plan
./install.sh migrate
```

`migrate` provisions the locked tools before it snapshots and activates the
repository configuration. If provisioning fails, it does not replace the shell
startup files. Use `./install.sh restore` to return to the exact pre-activation
files, then open a fresh terminal. See [docs/cutover.md](docs/cutover.md) for the
managed paths, drift behavior, and recovery contract.

## Permanent plain-file migration

1. Install mise 2026.9.1 or newer using its
   [official installation instructions](https://mise.jdx.dev/installing-mise.html).
   Its binary must be reachable as `mise`, normally in `~/.local/bin`. Existing
   macOS Homebrew installations can instead supply it with `brew install mise`.
   Include the applicable Homebrew paths from `.zshrc.local.example` when using
   that route. The bootstrap binary is an external prerequisite, not managed by
   its own tool list.
2. Back up the existing `.zshrc`, `.zprofile`, optional `.zshrc.local`, and the
   destination mise, Starship, and Atuin config files. Record which destinations
   did not exist so rollback can restore absence. Preserve permissions. Keep
   backups outside a public Git repository.
3. Review the plain files, then copy mise configuration first, from this folder:

   ```sh
   mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/mise"
   install -m 600 config/mise/config.toml "${XDG_CONFIG_HOME:-$HOME/.config}/mise/config.toml"
   install -m 600 config/mise/mise.lock "${XDG_CONFIG_HOME:-$HOME/.config}/mise/mise.lock"
   ```

4. Run `mise install --locked` from a directory outside any project. Inspect
   `mise config ls` first so project configurations do not join the installation.
   Do not continue if installation fails. No global Gradle install is needed.
5. Copy the remaining files only after successful installation:

   ```sh
   mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/atuin"
   install -m 600 config/starship.toml "${XDG_CONFIG_HOME:-$HOME/.config}/starship.toml"
   install -m 600 config/atuin/config.toml "${XDG_CONFIG_HOME:-$HOME/.config}/atuin/config.toml"
   install -m 600 .zprofile "${ZDOTDIR:-$HOME}/.zprofile"
   install -m 600 .zshrc "${ZDOTDIR:-$HOME}/.zshrc"
   ```

   Use `.zshrc.local.example` to retain the required launcher paths. Preserve
   the existing Vim, Git, IdeaVim, Codex, SSH, and application files. Replacement
   of `.zprofile` removes the old SDKMAN Java assignment; add the Obsidian CLI
   path to the local file if it is wanted. The observed `.zshenv` was empty.
6. Open a new terminal application session and check `mise which java`,
   `java -version`, `node --version`, `kast`, `idea`, `jbcontext`, Ctrl-R,
   `wt switch`, Git completion, and a representative repository build. IDEs may
   need their SDK path changed explicitly; shell activation cannot configure
   an already running GUI process.
7. After that check, retire the unused Apollo/Artemis and dotfiles startup code
   from their source repositories in a separate reviewed change. Their current
   worktrees contain unrelated edits and Apollo contains agent/application
   assets. Do not delete those wholesale. Keep old package installations until
   ownership of each needed executable has been accounted for.

Copying the same files again converges to the same configuration. There are no
managed blocks to duplicate. Rollback restores the backed-up files, removes only
new configuration files introduced by the migration, and starts a new terminal
application session. Tool downloads can remain until rollback is verified.

## Project toolchains and daily maintenance

The global Java and Node pins match the inspected local selection, Temurin
25.0.2 and Node 26.0.0. These are continuity defaults, not an assertion that
every repository supports them. In each repository, inspect its declared JDK
and Node requirements before using `mise use --pin` to write project pins.
Then generate `mise.lock` for its actual CI and developer platforms.

Keep Gradle's wrapper. A mise task may invoke `./gradlew test`, but should not
reimplement Gradle's dependencies or caching. Prefer mise to own Python
interpreters and uv to own virtual environments and Python packages. Keep
Rustup until repositories' `rust-toolchain.toml`, target, and component needs
are explicitly accounted for. Consolidation should not break those contracts.

Use `mise outdated` to review updates. Change exact pins deliberately and run
`mise lock --global --platform macos-arm64,macos-x64,linux-x64,linux-arm64`
before `mise install --locked`. Copy the resulting native config and lockfile
back to this directory for version control. No scheduled updater is required.

With `auto_install = false`, mise 2026.9.1 can warn about a missing tool in
`mise exec` and then execute another binary already on PATH. Do not treat
`mise exec` alone as proof of complete provisioning. Install successfully first
or inspect `mise which <tool>`. The configured shims separately reject missing
versions because automatic installation and system fallback are disabled.

For history, use Ctrl-R to retrieve and edit commands rather than turning every
repeated command into a function. Review `atuin stats` occasionally. Add a
shortcut only when its name and behavior fit in a few obvious lines. Put build
and release procedures in the repository where they can be tested and shared.

## Verification

`check.py` exercises the actual tools offline in a disposable home with spaces
in its path. Supply a disposable mise installation containing these pins:

```sh
python3 check.py --mise /path/to/mise --data-dir /path/to/disposable/mise/data
```

It checks lock/config agreement and checksums for four platforms, noninteractive
silence, login shims, Node/Java versions and `JAVA_HOME`, missing-tool failure,
exit-code propagation, repeated sourcing, preservation of an existing hook,
Atuin bindings, quoted navigation/search arguments, a real Worktrunk directory
switch in a temporary Git repository, and Starship rendering. It does not read
live credentials or Atuin history. Python 3.11+ is only a verification dependency.

The pinned tools were installed with mise 2026.9.1 in a disposable macOS ARM64
home. The checks passed there. The downloaded mise archive matched the SHA-256
published in its release checksum file. Linux and Intel macOS execution remain
unverified. This directory contains no installed binaries or live state.

References: [mise configuration](https://mise.jdx.dev/configuration.html),
[lockfiles](https://mise.jdx.dev/dev-tools/mise-lock.html),
[settings](https://mise.jdx.dev/configuration/settings.html),
[Java](https://mise.jdx.dev/lang/java.html),
[Starship setup](https://starship.rs/guide/),
[Atuin initialization](https://docs.atuin.sh/main/reference/init/), and
[Worktrunk shell integration](https://worktrunk.dev/config/).

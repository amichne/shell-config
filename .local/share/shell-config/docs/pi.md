# Public Pi configuration

Follow [amosblomqvist/pi-config](https://github.com/amosblomqvist/pi-config)'s **pick individual pieces** approach. This repo keeps public preferences, pinned upstream package references, local agent profiles, and an optional `/verify` template. It does not vendor third-party extension code. `ask-user-question.ts`, `web-fetch`, and `pi-lsp` work without tmux; interactive subagents require tmux.

## Preferences

`pi/settings.public.json` holds five non-secret preferences; `pi/packages.public.json` pins two Git packages and `@narumitw/pi-lsp`. The local `~/.pi/agent/settings.json` remains Pi's real settings file; **do not replace it with either public file**. It also contains machine-specific paths, model/provider choices, and other package selections. To preview and merge the managed values:

```sh
python3 pi/sync-settings.py
python3 pi/sync-settings.py --apply
```

Run from this checkout. Preview prints **only** the managed values, not your local settings. Apply overwrites only the five public preferences, replaces older references to the three managed packages, and preserves unrelated packages (including `pi-dictate`), other local settings, and file permissions. It refuses a missing, malformed, or symlinked target. Before applying, make a private local backup if you need an undo point. For a different Pi agent directory, pass `--target /path/to/agent/settings.json` to both commands. Re-run after changing either public file; run `/reload` in an open Pi session.

Keep `auth.json`, `models-store.json`, sessions, trust decisions, private model endpoints, credentials, and local path lists **out of Git**. Do not copy the entire `~/.pi/agent` directory into this repository. Review every addition before committing it.

## Install the selected extensions

After merging settings, install only the two pinned Git packages (not all packages in your account):

```sh
pi update git:github.com/amosblomqvist/pi-interactive-subagents@c3e8b53c0754ae5ccc19fdab5a7481ec039bc2f7
pi update git:github.com/amosblomqvist/pi-config@f82da563ab05d66729492d64c7ed4e96db3663f3
```

`pi-config` is registered with all of its resources disabled: only the two selected extensions are enabled through links below. The upstream web-fetch lockfile did not work with the installed npm version, so install its dependencies without modifying the pinned checkout's lockfile; `--ignore-scripts` avoids third-party install hooks:

```sh
agent_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
npm install --prefix "$agent_dir/git/github.com/amosblomqvist/pi-config/extensions/web-fetch" --ignore-scripts --no-save --package-lock=false --no-audit --no-fund
mkdir -p "$agent_dir/extensions" "$agent_dir/agents"
ln -s ../git/github.com/amosblomqvist/pi-config/extensions/ask-user-question.ts "$agent_dir/extensions/ask-user-question.ts"
ln -s ../git/github.com/amosblomqvist/pi-config/extensions/web-fetch "$agent_dir/extensions/web-fetch"
for name in scout researcher worker; do
  ln -s "$(pwd)/pi/agents/$name.md" "$agent_dir/agents/$name.md"
done
```

Run the link commands from this checkout. The `ln` commands **do not replace existing paths**; inspect any conflict rather than forcing a link. The three profiles override the upstream defaults, which select an OpenRouter model, so subagents instead use your current Pi default model. They also narrow the scout to read-only work and make the worker a non-nesting agent. The researcher uses `web_fetch` only (no search extension installed). Upstream extensions run with your account's permissions; review the pinned code before enabling it on another machine. On upgrades, re-run the web-fetch dependency install after updating the pin.

Install the pinned LSP package separately:

```sh
pi update npm:@narumitw/pi-lsp@0.49.8
```

Or, on a fresh account before syncing settings, use `pi install npm:@narumitw/pi-lsp@0.49.8`. It provides `/lsp`, `lsp_diagnostics`, and `lsp_fix`; it does **not** install language-server executables. No tmux is needed for this package.

### Python, JavaScript/TypeScript, and Kotlin servers

`config/mise/config.toml` pins **ty 0.0.83** and **Ruff 0.16.8** for Python and **Biome 2.5.14** for JS/TS. Install them with the existing global mise config:

```sh
mise install ty@0.0.83 ruff@0.16.8 biome@2.5.14
```

On macOS, install the official JetBrains Kotlin language server separately using its Homebrew formula (currently **263.4702.0**). If Homebrew refuses to load the untrusted tap, inspect the specific `jetbrains/utils/kotlin-lsp` formula first; trust **only that formula**, not the whole tap:

```sh
brew trust --formula jetbrains/utils/kotlin-lsp
brew install jetbrains/utils/kotlin-lsp
```

The Kotlin server is alpha, large (about 1.2 GB installed), and currently focused on JVM Gradle/Maven projects; Kotlin Multiplatform support is not yet complete. On Linux, use the standalone distribution from [Kotlin LSP releases](https://github.com/Kotlin/kotlin-lsp/releases) rather than this macOS formula.

From this checkout, link the four-server config only if no user config already exists:

```sh
ln -s "$(pwd)/pi/pi-lsp.json" "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/pi-lsp.json"
```

This selects only those four servers and **replaces pi-lsp's entire built-in catalog** for the user; project `.pi/pi-lsp.json` can override it. Inspect an existing target instead of replacing it. Start a fresh terminal/Pi session after updating mise so the process inherits the new PATH. Run `/lsp` to confirm the four commands report `ready`. Pi starts servers on demand; this check does not prove indexing or project diagnostics. Biome supplies JS/TS lint diagnostics, **not** TypeScript's full semantic typechecking; keep running project `tsc`, lint, Gradle, and test tasks as appropriate.

## Optional prompt

To make `/verify` available globally without replacing existing Pi resources:

```sh
mkdir -p "$HOME/.pi/agent/prompts"
ln -s "$(pwd)/pi/prompts/verify.md" "$HOME/.pi/agent/prompts/verify.md"
```

Run from this checkout. `ln -s` refuses to replace an existing prompt with that name; inspect it instead of forcing the link. Run `/reload` or restart Pi, then try `/verify "your claim"`. To remove only this link, verify where it points before unlinking it. This prompt adapts the verification idea from the upstream `prompt-snippets` extension without installing its runtime or keybindings.

## Run subagents with tmux

`ask_user_question` and `web_fetch` work in ordinary Pi sessions. For `subagent`, start Pi in a tmux session:

```sh
tmux new -A -s pi 'pi'
```

The essentials: `Ctrl+B`, then `D` detaches; `tmux attach -t pi` returns later. Use `/subagent scout investigate this directory` to try a read-only child. The widget shows progress; no custom tmux layout or keybindings are required. Do not launch another tmux server inside tmux.

For tmux 3.5+, Pi's [tmux guide](https://github.com/earendil-works/pi/blob/main/docs/tmux.md) recommends these two lines in your own `~/.tmux.conf` so modified Enter keys work:

```tmux
set -g extended-keys on
set -g extended-keys-format csi-u
```

They take effect in a **new tmux server**, not existing sessions. Do not kill a server with running work just to apply them. No tmux file is installed by this repository.

## Check

```sh
python3 tests/pi-settings.py
```

The test uses temporary settings files; it does not read or change your Pi account state. The top-level `install.sh` does not manage Pi files.

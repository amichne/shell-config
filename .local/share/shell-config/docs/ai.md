# One-shot AI commands

`ai` selects an existing AI harness, sends one prompt, and returns its final
response. It does not own authentication, model APIs, conversations, or an agent
runtime. Configuration supplies executable argument arrays rather than shell
command strings.

```sh
ai --config                         # fzf: harness → model → supported effort
ai ask                              # multiline input; Ctrl-D on an empty line sends
ai ask --prompt 'Explain this error'
cat build.log | ai ask 'Identify the first meaningful failure'
ai ask --edit                       # compose in the configured editor
ai commit                           # propose a message, review, commit staged changes
ai commit --dry-run                 # generate a message without committing
ai branch 'Make model discovery configurable'
ai script --prompt 'List files larger than 100 MB without deleting anything'
ai script --file request.md > inspect.sh
```

`ai script --file` reads **prompt text**, not executable code. Script generation
only writes source to stdout; `ai` never executes the returned script.

## Install

Use the repository's existing Node and fzf installations. No npm packages,
compiler, shell plugin, or startup change is needed. Node 22.16 is the tested
minimum here; the repository already selects Node 26. Git is required for Git
actions, and fzf is required only for `ai --config`. Install and authenticate the
chosen harness separately using its native installation and login process.

From this repository, after reviewing any existing `~/.local/bin/ai`:

```sh
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/ai"
runtime_dir="${XDG_DATA_HOME:-$HOME/.local/share}/shell-config/ai"
mkdir -p "$HOME/.local/bin" "$config_dir" "$runtime_dir"
install -m 755 bin/ai "$HOME/.local/bin/ai"
install -m 644 ai/*.mjs "$runtime_dir/"
# Preserve a configuration that you have already customized.
if [ ! -e "$config_dir/config.json" ]; then
    install -m 600 config/ai/config.json "$config_dir/config.json"
fi
ai --config
```

`~/.local/bin` is already on this repository's shell PATH. Deployment uses the
same plain-file approach as the other shell configuration. To try the source
without installing it:

```sh
AI_RUNTIME_DIR="$PWD/ai" AI_CONFIG="$PWD/config/ai/config.json" ./bin/ai --config
AI_RUNTIME_DIR="$PWD/ai" AI_CONFIG="$PWD/config/ai/config.json" ./bin/ai ask
```

The launcher accepts `AI_RUNTIME_DIR` to relocate the modules. There is no
checkout path embedded in the installed command.

## Select a harness, model, and effort

The selector displays configured harnesses, then that harness's models, then
that model's supported efforts. `(harness default)` omits the corresponding
flag. Selecting the default model also leaves effort at the harness default:
`ai` cannot infer the capabilities of an unknown native default model.

Selection is saved only after all choices succeed. Cancelling leaves the previous
selection unchanged. Per-command overrides do not rewrite the saved selection:

```sh
ai ask --harness codex --model gpt-5.4 --effort high --prompt 'Explain this design'
ai ask --harness pi --model openai/gpt-5.4 --effort high --prompt 'Explain this design'
ai ask --harness copilot --model default --prompt 'Explain this design'
```

Changing the harness clears a previous model and effort unless explicitly
supplied. Changing the model clears a previous effort. Invalid model/effort
combinations fail before the generation command starts, rather than silently
substituting a different choice.

The starter model entries are editable examples, not an account entitlement
check or a guarantee that a particular installed CLI supports them. Native
harness errors are preserved. Use each harness's own model selection tools to
check availability, then edit the catalog or supply a discovery command.

## Customize the adapter

Configuration is read from `--config-file PATH`, then `AI_CONFIG`, then
`${XDG_CONFIG_HOME:-$HOME/.config}/ai/config.json`. There is one configuration
file, not a set of merged profiles or project-local executable overlays.

Add an entry to `harnesses`; no JavaScript change is required:

```json
{
  "my-harness": {
    "command": ["/absolute/path/to/my-agent", "--headless", "--quiet"],
    "prompt": {"kind": "stdin", "args": ["-"]},
    "modelArgs": ["--model", "{model}"],
    "effortArgs": ["--reasoning", "{effort}"],
    "models": [
      {"id": "model-a", "efforts": ["low", "high"]},
      {"id": "model-b", "efforts": []}
    ],
    "env": {"MY_AGENT_PROFILE": "work"}
  }
}
```

This example's flags belong to a hypothetical executable; configure the flags
that your actual command supports. Executables can be absolute paths, `~/`
paths, paths relative to the configuration directory, or names resolved on
PATH. Each array element remains one argument, including spaces and newlines.
No `eval`, shell interpolation, or recursive template expansion is performed.
Environment values are literal strings; inherited credentials remain the native
harness's responsibility.

Three prompt transports cover headless CLIs:

| Transport | Configuration | Behavior |
| --- | --- | --- |
| Standard input | `{"kind":"stdin","args":["-"]}` | Write the prompt to stdin. Use an empty argument array for implicit stdin. |
| Argument | `{"kind":"argument","args":["--prompt","{prompt}"]}` | Pass the entire prompt as one argument. It can be visible in process listings. |
| File | `{"kind":"file","args":["--prompt-file","{promptFile}"]}` | Write a private temporary UTF-8 file and pass its path. |

The invocation order is `command`, optional `modelArgs`, optional `effortArgs`,
then `prompt.args`. Only the corresponding placeholders are substituted in
each argument group. A custom executable can normalize unusual CLI ordering or
structured output. Its contract is simple: read the configured input, emit only
the final response on stdout, log diagnostics to stderr, and exit nonzero on
failure. The built-in adapters use native plain-output modes instead of parsing
agent event streams.

### Static or dynamic model catalogs

Use `models` for a curated list. To discover models dynamically, replace it with
`modelsCommand`; the two fields are mutually exclusive:

```json
{"modelsCommand": ["./discover-models", "--json"]}
```

The command returns the same JSON array as `models`:

```json
[
  {"id": "model-a", "efforts": ["low", "medium", "high"]},
  {"id": "model-b", "efforts": []}
]
```

Discovery runs on demand, including when validating a noninteractive invocation,
from the configuration directory with the harness's environment. It has a
10-second maximum and the configured output limit. Failure is explicit; there
is no fallback to a stale catalog or automatic provider login. The POC does not
ship provider-specific discovery programs or parse private model caches.

### Instructions and input UI

`instructions.ask`, `instructions.commit`, `instructions.branch`, and
`instructions.script` are either strings or file references. Each file is read
relative to the configuration directory, so shared instructions stay editable:

```json
{"commit": {"file": "instructions/commit.md"}}
```

Place that entry inside `instructions`. Commit instructions receive the staged
diff and any extra text supplied to `ai commit`. Instructions describe the
wanted result; they do not grant execution permissions to the wrapper.

`ui.fzf` is an argv array controlling fzf's command, layout, colors, and bindings.
The selector additionally enforces single selection and its row format.
`ui.editor` is an argv array, such as `["vim"]` or `["code", "--wait"]`.
`--edit` appends a temporary input-file path and submits after the editor exits
successfully. Without an explicit editor, `VISUAL`, then `EDITOR`, then `vim`
is used as an executable name; put editor flags in `ui.editor`, not a shell string.

The built-in composer uses Enter for a newline, Ctrl-D on an empty line to send,
and Ctrl-C to cancel. To substitute a richer input UI or different submit binding,
set `ui.inputCommand` to an argv array. It reads the real terminal through stdin,
writes only prompt text to stdout, and exits nonzero to cancel. The input command
owns its terminal keybindings; fzf remains the choice selector, not a text editor.

Positional text, `--prompt`, `--file`, and `--edit` are mutually exclusive. Piped
stdin can augment positional text or `--prompt`; `--file` and `--edit` instead
provide the complete input. For text starting with a dash, use
`--prompt='-leading text'` or `ai ask -- '-leading text'`.

Selection lives separately in
`${XDG_STATE_HOME:-$HOME/.local/state}/ai/selection.json`, keyed by the canonical
configuration path. Writes use a private temporary file and atomic rename.
`ai --config` never reformats or rewrites the hand-maintained configuration.
Concurrent selection writers are not a transactional configuration service.

## Effects and failure behavior

`ai commit` reads only the staged diff, generates a message, displays it, and
asks before calling native `git commit --file`. It never stages files, amends,
pushes, or disables Git hooks/signing. `ai branch` validates exactly one branch
name and asks before native `git switch -c`. `--dry-run` generates a proposal
without applying it; `--yes` explicitly skips interactive review. Without a
terminal, Git mutation requires `--yes`; otherwise use `--dry-run`.

Before generation, Git actions capture HEAD, the current symbolic branch, and
the index tree. They check that state again before mutation and reject stale
proposals. This protects against changes during generation/review; it is **not**
an atomic Git transaction across other processes or hook execution. Hooks retain
their native ability to modify state. Even proposal generation can cause native
Git bookkeeping such as `write-tree`; dry-run means no commit/branch application.

The wrapper never evaluates an answer as a command. Nevertheless, configured
executables and inherited harness settings are trusted code, not sandboxed
plugins. The starter Codex invocation uses read-only sandbox mode; Copilot and
Pi restrict their built-in tool access. These flags are not a common isolation
proof: inherited MCP servers, integrations, and native policies remain owned by
the harness. Review those settings before supplying sensitive content. Prompts
and staged diffs are sent to the selected harness/provider, with no automatic
secret redaction. No unrestricted/yolo execution flag is enabled in the presets.

`timeoutMs`, `maxInputBytes`, and `maxOutputBytes` are configurable positive
integers. Oversized input or output fails rather than being silently truncated.
The complete prompt, including instructions and a staged diff, must fit the input
limit. Successful generation emits only the final answer on stdout; diagnostics
and reviews use stderr. Failed partial responses are not emitted as answers.
Native nonzero exit codes propagate; missing commands return 127, timeout 124,
Ctrl-C 130, and configuration/input errors 2. Native Git hooks are not subjected
to the AI timeout. Harness cancellation targets its process group on POSIX.
Temporary prompt, editor, and commit-message files are private and cleaned up.

## Verification and scope

```sh
node --test tests/ai.test.mjs
python3 tests/ai-ui.py
# Exercise an actual installed selector, in addition to the fixture protocol:
python3 tests/ai-ui.py --fzf "$(command -v fzf)"
```

The implementation was exercised on Linux with Node 22.16 and Python 3.13:
32 automated tests passed, including real subprocesses and temporary Git
repositories. Coverage includes transport quoting, dynamic catalogs, invalid
selection rejection, timeouts/process groups, interrupted execution, bounded
I/O, failure propagation, staged-only commits, stale Git state rejection,
native hook failure, branch validation, and script output that is never executed.
PTY checks passed for multiline submission, cancellation, Git confirmation, and
selector persistence/cancellation using a deterministic selector fixture.

**Not verified here:** real fzf rendering/keybindings, authenticated Codex/Pi/
Copilot calls, account-specific model availability, macOS execution, or Node 26
execution. The existing full `check.py` suite was not run because its provisioned
mise/Zsh toolchain was unavailable; this change leaves startup, tool pins, and
lockfiles untouched. These files are a reviewable POC, not a claim of deployment
to the live home directory.

## Why a small adapter rather than another runtime

[Aida](https://github.com/metalagman/aida) is the closest researched existing
solution: it generates shell commands through existing agents and confirms
execution, using an ACP/provider runtime. This POC instead keeps the narrower
contract of arbitrary single-shot executables, a shared selector, and explicit
ask/commit/branch/script actions. It reuses the already selected Node and fzf
rather than introducing another model or session framework.

Native adapter references, inspected September 5, 2026:

- [Codex noninteractive execution](https://developers.openai.com/codex/noninteractive/)
  and [CLI reference](https://developers.openai.com/codex/cli/reference/).
- [Copilot CLI command reference](https://docs.github.com/en/copilot/reference/cli-command-reference).
- [Pi coding-agent CLI](https://github.com/earendil-works/pi/tree/main/packages/coding-agent).
- [fzf](https://github.com/junegunn/fzf).

#!/usr/bin/env python3
"""Offline checks with real installed tools and disposable home/XDG directories.

Usage: python3 check.py --mise /path/to/mise --data-dir /path/to/mise/data
Provision the tools in config/mise/config.toml before running these checks.
The supplied data directory must be a disposable installation, not live state.
"""

import argparse
import json
import os
import fcntl
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import tomllib


def check_terminal(zsh, env, cwd):
    """Exercise real ZLE redraws and keypresses in a disposable PTY."""
    pid, terminal = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execve(zsh, [zsh, '-i'], env)
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 100, 0, 0))
    ansi = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
    pending = ''

    def send(text):
        os.write(terminal, text.encode())

    def expect(needle, raw=False):
        nonlocal pending
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            offsets = []
            cursor = 0
            if not raw:
                for escape in ansi.finditer(pending):
                    offsets.extend(range(cursor, escape.start()))
                    cursor = escape.end()
                offsets.extend(range(cursor, len(pending)))
            clean = pending if raw else ''.join(pending[i] for i in offsets)
            if needle in clean:
                end = clean.index(needle) + len(needle)
                # Retain subsequent prompt bytes already received in this read.
                raw_end = end if raw else offsets[end - 1] + 1
                result, pending = clean[:end], pending[raw_end:]
                return result
            if select.select([terminal], [], [], .1)[0]:
                pending += os.read(terminal, 65536).decode(errors='replace')
        raise AssertionError({'expected': needle, 'terminal': pending[-4000:]})

    try:
        startup = expect('\x1b[?2004h', raw=True)
        assert 'shell-config: stage=' not in startup, startup
        send("[[ $_SHELL_CONFIG_LOADED == 1 && $(bindkey '^R') == *atuin* && $(bindkey '^T') == *fzf-file-widget* && $(bindkey '^[c') == *fzf-cd-widget* ]] && print TERMINAL_READY\n")
        expect('\r\nTERMINAL_READY\r\n')
        expect('\x1b[?2004h', raw=True)
        send("shell_check_capture() { print -r -- \"ZLE_BUFFER=$BUFFER\" \"ZLE_SUGGEST=$POSTDISPLAY\" \"ZLE_HIGHLIGHT=${(j:;:)region_highlight}\"; zle redisplay; }; zle -N shell_check_capture; ZSH_AUTOSUGGEST_IGNORE_WIDGETS+=(shell_check_capture); bindkey '^X' shell_check_capture\n")
        expect('\x1b[?2004h', raw=True)
        # A unique file with a space must be completed as one shell argument.
        (cwd / 'completion fixture').write_text('fixture\n')
        send('print -r -- completion\t')
        time.sleep(.15)
        send('\n')
        expect('\r\ncompletion fixture\r\n')
        expect('\x1b[?2004h', raw=True)
        send('print -r -- history-fixture-unique\n')
        expect('\r\nhistory-fixture-unique\r\n')
        expect('\x1b[?2004h', raw=True)
        send('print -r -- history-fixt')
        time.sleep(.4)
        send('\x18')
        suggestion = expect('ZLE_HIGHLIGHT=')
        assert 'ZLE_SUGGEST=ure-unique' in suggestion, suggestion
        send('\x1b[C\n')
        expect('\r\nhistory-fixture-unique\r\n')
        expect('\x1b[?2004h', raw=True)
        send('definitely_missing_shell_command')
        time.sleep(.15)
        send('\x18')
        expect('ZLE_HIGHLIGHT=')
        highlight = expect('\r\n')
        assert 'fg=red' in highlight, highlight
        send('\x03')
        expect('\x1b[?2004h', raw=True)
        send('false\n')
        expect('\x1b[38;2;255;85;85m', raw=True)
    finally:
        os.kill(pid, signal.SIGTERM)
        os.close(terminal)
        os.waitpid(pid, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mise", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    mise = args.mise.resolve(strict=True)
    data = args.data_dir.resolve(strict=True)
    zsh = shutil.which("zsh")
    git = shutil.which("git")
    if not zsh or not git:
        parser.error("Zsh and Git are prerequisites")
    config = tomllib.loads((source / "config/mise/config.toml").read_text())
    exact_pin = re.compile(r"^(?:[A-Za-z][A-Za-z0-9._-]*-)?\d+\.\d+\.\d+(?:[+.-][A-Za-z0-9.]+)*$")
    for name, version in config["tools"].items():
        assert isinstance(version, str) and exact_pin.fullmatch(version), (name, version)
    assert config["settings"]["auto_install"] is False
    assert config["settings"]["not_found_system_fallback"] is False
    for file in (source / "config").rglob("*.toml"):
        tomllib.loads(file.read_text())

    with tempfile.TemporaryDirectory(prefix="shell checks with spaces ") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        shutil.copy2(source / ".zshrc", home / ".zshrc")
        shutil.copy2(source / ".zprofile", home / ".zprofile")
        shutil.copytree(source / "config", root / "config")
        shutil.copytree(source / "prompt", root / "data/shell-config/prompt")
        (home / ".local/bin").mkdir(parents=True)
        (home / ".local/bin/mise").symlink_to(mise)
        (home / ".local/bin/shell-prompt").symlink_to(source / "bin/shell-prompt")
        (home / ".local/bin/prompt-editor").symlink_to(source / "bin/prompt-editor")
        # The captured PATH is data: spaces and shell metacharacters must survive.
        kept = [str(root / 'launcher with spaces'), str(root / '$(touch UNEXPECTED)'),
                '/Applications/Ghostty.app/Contents/MacOS',
                '/Applications/Obsidian.app/Contents/MacOS']
        retired = [str(home / '.sdkman/candidates/java/current/bin'),
                   str(home / '.sdkman/candidates/kotlin/current/bin'),
                   str(home / '.docker/bin'), str(home / 'code/apollo/artemis/bin'),
                   '/Applications/VMware Fusion.app/Contents/Public']
        (home / '.zshpath').write_text(':'.join(kept + retired + kept) + '\n')
        env = {
            "HOME": str(home),
            "PATH": os.pathsep.join((str(Path(git).parent), "/usr/bin", "/bin")),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_STATE_HOME": str(root / "state"),
            "MISE_DATA_DIR": str(data),
            "MISE_CACHE_DIR": str(root / "cache/mise"),
            "MISE_OFFLINE": "1",
            "TERM": "xterm-256color",
            "LC_ALL": "C",
        }

        def run(command, expected=0, cwd=root):
            result = subprocess.run(command, env=env, cwd=cwd, text=True,
                                    capture_output=True, timeout=45)
            assert result.returncode == expected, {
                "command": command, "expected": expected,
                "exit": result.returncode, "stdout": result.stdout,
                "stderr": result.stderr,
            }
            return result

        run([zsh, "-n", str(home / ".zshrc")])
        run([zsh, "-n", str(home / ".zprofile")])
        quiet = run([zsh, "-fc", 'source "$HOME/.zshrc"'])
        assert not quiet.stdout and not quiet.stderr, "noninteractive startup output"
        login = run([zsh, "-lc", "command -v node"])
        assert login.stdout.strip() == str(data / "shims/node"), login.stdout
        login_path = run([zsh, '-lc', 'print -rl -- $path']).stdout.splitlines()
        assert all(login_path.count(entry) == 1 for entry in kept), login_path
        assert not set(retired).intersection(login_path), login_path
        assert not (root / 'UNEXPECTED').exists(), 'PATH data was executed'
        run([str(mise), "exec", "--", "sh", "-c", "exit 37"], expected=37)
        versions = run([str(mise), "exec", "--", "sh", "-c",
                        'node --version && java -version && test -x "$JAVA_HOME/bin/java" && eza --version'])
        assert "v26.0.0" in versions.stdout and "v0.23.5" in versions.stdout
        assert '"25.0.2"' in versions.stderr
        kotlin = run([str(mise), 'exec', '--', 'kotlinc', '-version'])
        assert '2.1.21' in kotlin.stderr, kotlin.stderr
        config_path = root / "config/mise/config.toml"
        original = config_path.read_text()
        try:
            config_path.write_text(original.replace('node = "26.0.0"', 'node = "0.0.0"'))
            missing = subprocess.run([str(data / "shims/node"), "--version"],
                                     env=env, cwd=root, capture_output=True, text=True, timeout=45)
            assert missing.returncode != 0, "missing runtime shim used system Node"
        finally:
            config_path.write_text(original)

        repo = root / "repo with spaces"
        run([git, "init", "-b", "main", str(repo)])
        run([git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "--allow-empty", "-m", "fixture"], cwd=repo)
        (repo / "sub directory").mkdir()
        (repo / "sample file").write_text("needle\n")
        script = r'''
existing_hook() { :; }
precmd_functions=(existing_hook)
source "$HOME/.zshrc" || exit
[[ $_SHELL_CONFIG_LOADED == 1 ]] || exit 20
(( $+functions[_zsh_highlight] && $+functions[_zsh_autosuggest_start] )) || exit 34
[[ ${_comps[git]} == _git ]] || exit 35
[[ ${_comps[mise]} == _mise && ${_comps[atuin]} == _atuin ]] || exit 38
[[ -o autocd && -o interactivecomments && ! -o flowcontrol ]] || exit 36
[[ -o sharehistory && -o histignorespace ]] || exit 37
[[ ${precmd_functions[(Ie)existing_hook]} != 0 ]] || exit 21
before_path=$PATH
before_hooks="${(j: :)precmd_functions}|${(j: :)preexec_functions}|${(j: :)chpwd_functions}"
source "$HOME/.zshrc" || exit
[[ $PATH == $before_path ]] || exit 22
[[ "${(j: :)precmd_functions}|${(j: :)preexec_functions}|${(j: :)chpwd_functions}" == $before_hooks ]] || exit 23
(( $+functions[wt] )) || exit 24
[[ $(bindkey '^R') == *atuin* ]] || exit 25
[[ $(bindkey '^[[A') != *atuin* ]] || exit 26
(( ! $+functions[git] && ! $+functions[rmdir] )) || exit 27
[[ ${aliases[ls]} == 'eza --smart-group --group-directories-first --icons=auto' ]] || exit 43
[[ ${aliases[l]} == 'eza --smart-group --group-directories-first --icons=auto --all' ]] || exit 44
[[ -z ${GITHUB_PERSONAL_ACCESS_TOKEN-}${CODEX_GITHUB_PERSONAL_ACCESS_TOKEN-} ]] || exit 28
root=$PWD
cd -- 'sub directory' || exit
rr || exit
[[ $PWD == $root ]] || exit 29
f 'sample file' needle > /dev/null || exit
f 'sample file' absent > /dev/null
[[ $? == 1 ]] || exit 30
f > /dev/null 2>&1
[[ $? == 2 ]] || exit 31
wt switch --create fixture --no-hooks > /dev/null || exit
[[ $PWD != $root ]] || exit 32
[[ $(git branch --show-current) == fixture ]] || exit 33
prompt_context=$(shell-prompt context --json) || exit 39
[[ $prompt_context == *'"kind":"repository"'* ]] || {
    print -u2 -- "stage=prompt-context outcome=unexpected value=${prompt_context[1,160]}"
    exit 39
}
starship prompt --status=1 > /dev/null || exit
print -- CHECKS_OK
'''
        interactive = run([zsh, "-fic", script], cwd=repo)
        assert interactive.stdout.strip() == "CHECKS_OK", interactive.stdout
        assert "[ERROR]" not in interactive.stderr and "[WARN]" not in interactive.stderr, interactive.stderr
        check_terminal(zsh, env, root)

        # Missing mise reports failure without disabling native completion/ZLE.
        (home / ".local/bin/mise").unlink()
        env["PATH"] = "/usr/bin:/bin"
        failed = run([zsh, "-fic", 'source "$HOME/.zshrc"'], expected=127)
        assert "stage=mise outcome=missing-command" in failed.stderr
        degraded = run([zsh, '-fic', '''
source "$HOME/.zshrc"
[[ $? == 127 && ${_comps[git]} == _git ]] || exit 40
(( $+functions[_zsh_highlight] )) || exit 41
before_hooks="${(j: :)precmd_functions}"
source "$HOME/.zshrc"
[[ $? == 127 && "${(j: :)precmd_functions}" == $before_hooks ]] || exit 42
'''])
        assert degraded.stderr.count('stage=mise outcome=missing-command') == 1

    print(json.dumps({"result": "passed", "pinned_tools": len(config["tools"]),
                      "runtime_platform": os.uname().sysname,
                      "checks": ["exact version pins", "noninteractive silence",
                                 "exit propagation", "runtime versions and JAVA_HOME",
                                 "missing dependency failure", "repeated sourcing",
                                 "existing hook preservation", "Atuin key bindings",
                                 "quoted paths and search exit codes", "Worktrunk changes directory",
                                 "typed Git prompt context", "Starship prompt",
                                 "captured PATH and retired path filtering",
                                 "PTY Tab completion, suggestions, highlighting, fzf bindings and exit status"]}, indent=2))


if __name__ == "__main__":
    main()

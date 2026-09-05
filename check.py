#!/usr/bin/env python3
"""Offline checks with real installed tools and disposable home/XDG directories.

Usage: python3 check.py --mise /path/to/mise --data-dir /path/to/mise/data
Provision the tools in config/mise/config.toml before running these checks.
The supplied data directory must be a disposable installation, not live state.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib


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
    lock = tomllib.loads((source / "config/mise/mise.lock").read_text())
    assert set(lock["tools"]) == set(config["tools"]), "lock/config tool mismatch"
    for name, version in config["tools"].items():
        entries = lock["tools"][name]
        assert len(entries) == 1 and entries[0]["version"] == version, name
        for platform in ("macos-arm64", "macos-x64", "linux-arm64", "linux-x64"):
            entry = entries[0]["platforms." + platform]
            assert entry["url"].startswith("https://"), (name, platform)
            assert entry["checksum"].startswith("sha256:"), (name, platform)
    for file in (source / "config").rglob("*.toml"):
        tomllib.loads(file.read_text())

    with tempfile.TemporaryDirectory(prefix="shell checks with spaces ") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        shutil.copy2(source / ".zshrc", home / ".zshrc")
        shutil.copy2(source / ".zprofile", home / ".zprofile")
        shutil.copytree(source / "config", root / "config")
        (home / ".local/bin").mkdir(parents=True)
        (home / ".local/bin/mise").symlink_to(mise)
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
        run([str(mise), "exec", "--", "sh", "-c", "exit 37"], expected=37)
        versions = run([str(mise), "exec", "--", "sh", "-c",
                        'node --version && java -version && test -x "$JAVA_HOME/bin/java"'])
        assert "v26.0.0" in versions.stdout and '"25.0.2"' in versions.stderr
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
starship prompt --status=1 > /dev/null || exit
print -- CHECKS_OK
'''
        interactive = run([zsh, "-fic", script], cwd=repo)
        assert interactive.stdout.strip() == "CHECKS_OK", interactive.stdout
        assert "[ERROR]" not in interactive.stderr and "[WARN]" not in interactive.stderr, interactive.stderr

        # Missing mise must stop initialization with a useful status and message.
        (home / ".local/bin/mise").unlink()
        env["PATH"] = "/usr/bin:/bin"
        failed = run([zsh, "-fic", 'source "$HOME/.zshrc"'], expected=127)
        assert "mise is missing" in failed.stderr

    print(json.dumps({"result": "passed", "locked_tools": len(config["tools"]),
                      "lock_platforms": 4, "runtime_platform": os.uname().sysname,
                      "checks": ["TOML and lock agreement", "noninteractive silence",
                                 "exit propagation", "runtime versions and JAVA_HOME",
                                 "missing dependency failure", "repeated sourcing",
                                 "existing hook preservation", "Atuin key bindings",
                                 "quoted paths and search exit codes", "Worktrunk changes directory",
                                 "Starship prompt"]}, indent=2))


if __name__ == "__main__":
    main()

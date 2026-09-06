#!/usr/bin/env python3
"""Exercise reversible shell-config cutover without touching the real home."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="shell cutover with spaces ") as temp:
        root = Path(temp)
        repo = root / "repo"
        home = root / "home"
        zdot = root / "z dot"
        config = root / "config"
        data = root / "data"
        state = root / "state"
        for directory in (repo, home, zdot, config, data, state):
            directory.mkdir()

        shutil.copy2(SOURCE / "install.sh", repo / "install.sh")
        sources = (
            ".zshrc", ".zprofile", "config/mise/config.toml",
            "config/mise/mise.lock", "config/starship.toml",
            "config/atuin/config.toml", "config/ai/config.json", "bin/ai",
            "ai/core.mjs", "ai/config.mjs", "ai/main.mjs", "ai/ui.mjs",
        )
        for name in sources:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"repository:{name}\n")
        (repo / "install.sh").chmod(0o755)

        (zdot / ".zshrc").write_text("original-zshrc\n")
        (config / "mise").mkdir()
        (config / "mise/config.toml").write_text("original-mise\n")
        (config / "ai").mkdir()
        (config / "ai/config.json").write_text("original-ai\n")
        (config / "ai/config.json").chmod(0o600)
        old_core = data / "shell-config/ai/core.mjs"
        old_core.parent.mkdir(parents=True)
        old_core.write_text("original-core\n")
        external = root / "external-starship.toml"
        external.write_text("original-starship\n")
        (config / "starship.toml").symlink_to(external)

        env = {
            **os.environ,
            "HOME": str(home),
            "ZDOTDIR": str(zdot),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "XDG_STATE_HOME": str(state),
        }

        def run(*args, expected=0):
            result = subprocess.run(
                [str(repo / "install.sh"), *args], env=env, text=True,
                capture_output=True, timeout=10,
            )
            assert result.returncode == expected, {
                "args": args, "expected": expected, "exit": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
            }
            return result

        subprocess.run(["sh", "-n", str(repo / "install.sh")], check=True)
        assert "no snapshot" in run("status").stdout
        run("activate")
        assert (zdot / ".zshrc").is_symlink()
        assert os.readlink(zdot / ".zshrc") == str(repo / ".zshrc")
        assert not (home / ".zshrc").exists(), "ZDOTDIR was ignored"
        assert (config / "ai/config.json").is_symlink()
        assert (home / ".local/bin/ai").is_symlink()
        assert "active from" in run("status").stdout

        # Restore validates the entire managed surface before changing anything.
        (zdot / ".zshrc").unlink()
        (zdot / ".zshrc").write_text("unexpected drift\n")
        failed = run("restore", expected=2)
        assert "changed since activation" in failed.stderr
        assert (config / "ai/config.json").is_symlink()
        (zdot / ".zshrc").unlink()
        (zdot / ".zshrc").symlink_to(repo / ".zshrc")

        run("restore")
        assert (zdot / ".zshrc").read_text() == "original-zshrc\n"
        assert not (zdot / ".zprofile").exists(), "original absence was not restored"
        assert (config / "mise/config.toml").read_text() == "original-mise\n"
        assert (config / "ai/config.json").read_text() == "original-ai\n"
        assert (config / "ai/config.json").stat().st_mode & 0o777 == 0o600
        assert old_core.read_text() == "original-core\n"
        assert (config / "starship.toml").is_symlink()
        assert os.readlink(config / "starship.toml") == str(external)

        # Every activation snapshots the current inactive configuration, so a
        # later restore returns to the state immediately before that cutover.
        (zdot / ".zshrc").write_text("original-zshrc-v2\n")
        run("toggle")
        assert (zdot / ".zshrc").is_symlink()
        run("toggle")
        assert (zdot / ".zshrc").read_text() == "original-zshrc-v2\n"
        assert "previous snapshot retained" in run("status").stdout

    print("CUTOVER_CHECKS_OK")


if __name__ == "__main__":
    main()

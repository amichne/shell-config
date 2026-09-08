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
        repo = (root / "repo").resolve()
        home = root / "home"
        zdot = root / "z dot"
        config = root / "config"
        data = root / "data"
        state = root / "state"
        tool_bin = root / "tool bin"
        for directory in (repo, home, zdot, config, data, state, tool_bin):
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
        mise_log = root / "mise.log"
        fake_mise = tool_bin / "mise"
        fake_mise.write_text("""#!/bin/sh
set -eu
printf 'config=%s\\n' "${MISE_GLOBAL_CONFIG_FILE-}" >> "$MISE_LOG"
printf 'cwd=%s\\n' "$PWD" >> "$MISE_LOG"
printf 'args=' >> "$MISE_LOG"
printf '%s|' "$@" >> "$MISE_LOG"
printf '\\n' >> "$MISE_LOG"
[ ! -e "$MISE_FAIL_FILE" ] || exit 42
""")
        fake_mise.chmod(0o755)

        env = {
            **os.environ,
            "HOME": str(home),
            "ZDOTDIR": str(zdot),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "XDG_STATE_HOME": str(state),
            "MISE_FAIL_FILE": str(root / "mise.fail"),
            "MISE_LOG": str(mise_log),
            "PATH": f"{tool_bin}{os.pathsep}{os.environ['PATH']}",
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
        before_plan = {
            path: (path.is_symlink(), os.readlink(path) if path.is_symlink() else path.read_bytes())
            for path in (zdot / ".zshrc", config / "mise/config.toml", config / "starship.toml")
        }
        assert "would provision locked tools" in run("plan").stdout
        after_plan = {
            path: (path.is_symlink(), os.readlink(path) if path.is_symlink() else path.read_bytes())
            for path in before_plan
        }
        assert after_plan == before_plan, "plan changed a managed target"
        assert not (state / "shell-config-cutover/snapshot").exists()

        Path(env["MISE_FAIL_FILE"]).touch()
        run("migrate", expected=42)
        assert after_plan == {
            path: (path.is_symlink(), os.readlink(path) if path.is_symlink() else path.read_bytes())
            for path in before_plan
        }, "failed provisioning changed a managed target"
        assert not (state / "shell-config-cutover/snapshot").exists()
        Path(env["MISE_FAIL_FILE"]).unlink()

        run("migrate")
        assert (zdot / ".zshrc").is_symlink()
        assert os.readlink(zdot / ".zshrc") == str(repo / ".zshrc")
        assert not (home / ".zshrc").exists(), "ZDOTDIR was ignored"
        assert (config / "ai/config.json").is_symlink()
        assert (home / ".local/bin/ai").is_symlink()
        assert "active from" in run("status").stdout
        log_after_migrate = mise_log.read_text()
        assert f"config={repo / 'config/mise/config.toml'}" in log_after_migrate
        assert f"cwd={repo}" in log_after_migrate
        assert "args=install|--locked|--dry-run|" in log_after_migrate
        assert "args=install|--locked|" in log_after_migrate
        assert "already active" in run("migrate").stdout
        assert mise_log.read_text() == log_after_migrate, "idempotent migration reprovisioned tools"

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

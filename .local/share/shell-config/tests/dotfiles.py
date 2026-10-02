#!/usr/bin/env python3
"""Exercise the bare Git migration in isolated repositories and temporary homes."""

import json
import os
from pathlib import Path
import pty
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "dotfiles.py"
GIT = shutil.which("git")
# These are the public leaves exported outside the runtime support directory.
# An installed check reconstructs only this allowlist, never a HOME tree scan.
HOME_SOURCES = (
    (".zshrc", ".zshrc"), (".zprofile", ".zprofile"),
    ("config/mise/config.toml", ".config/mise/config.toml"),
    ("config/starship.toml", ".config/starship.toml"),
    ("config/prompt.json", ".config/prompt.json"),
    ("config/atuin/config.toml", ".config/atuin/config.toml"),
    ("config/ai/config.json", ".config/ai/config.json"),
    ("bin/ai", ".local/bin/ai"), ("bin/config", ".local/bin/config"),
    ("bin/dotfiles", ".local/bin/dotfiles"),
    ("bin/shell-prompt", ".local/bin/shell-prompt"),
    ("bin/prompt-editor", ".local/bin/prompt-editor"),
    ("pi/pi-lsp.json", ".pi/agent/pi-lsp.json"),
    ("pi/agents/scout.md", ".pi/agent/agents/scout.md"),
    ("pi/agents/worker.md", ".pi/agent/agents/worker.md"),
    ("pi/agents/researcher.md", ".pi/agent/agents/researcher.md"),
    ("pi/prompts/verify.md", ".pi/agent/prompts/verify.md"),
    ("bin/shell-tools", ".local/bin/shell-tools"), ("bin/keys", ".local/bin/keys"),
    ("bin/project", ".local/bin/project"), ("bin/scratch", ".local/bin/scratch"),
    ("bin/work", ".local/bin/work"), ("bin/review", ".local/bin/review"),
    ("config/zsh/functions/y", ".config/zsh/functions/y"),
    ("config/zsh/functions/p", ".config/zsh/functions/p"),
    ("config/zsh/functions/rr", ".config/zsh/functions/rr"),
    ("config/zsh/functions/f", ".config/zsh/functions/f"),
    ("config/zsh/completions/_config", ".config/zsh/completions/_config"),
    ("config/nvim/init.lua", ".config/nvim/init.lua"),
    ("config/nvim/lua/editor/options.lua", ".config/nvim/lua/editor/options.lua"),
    ("config/nvim/lua/editor/keymaps.lua", ".config/nvim/lua/editor/keymaps.lua"),
    ("config/nvim/lua/editor/lsp.lua", ".config/nvim/lua/editor/lsp.lua"),
    ("config/nvim/lua/editor/plugins.lua", ".config/nvim/lua/editor/plugins.lua"),
    ("config/nvim/lua/editor/health.lua", ".config/nvim/lua/editor/health.lua"),
    ("config/yazi/yazi.toml", ".config/yazi/yazi.toml"),
    ("config/yazi/keymap.toml", ".config/yazi/keymap.toml"),
    ("config/work/config.json", ".config/work/config.json"),
)
INSTALLED = not (ROOT / ".zshrc").is_file()
INSTALLED_HOME = ROOT.parents[2] if INSTALLED else None
CHECKOUT_WRAPPER = INSTALLED_HOME / ".local/bin/dotfiles" if INSTALLED else ROOT / "bin/dotfiles"


def tree_snapshot(root, excluded=()):
    """Retain file contents, modes, symlink targets, and directory absence."""
    result = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(relative == name or name in relative.parents for name in excluded):
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[str(relative)] = ("SYMLINK", mode, os.readlink(path))
        elif path.is_file():
            result[str(relative)] = ("FILE", mode, path.read_bytes())
        elif path.is_dir():
            result[str(relative)] = ("DIRECTORY", mode)
    return result


class DotfilesTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(GIT, "Git is a prerequisite for this isolated check")
        temporary = tempfile.TemporaryDirectory(prefix="dotfiles tests with spaces ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source checkout"
        shutil.copytree(
            ROOT, self.source,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store", ".zshrc.local"),
        )
        if INSTALLED:
            self.assertEqual(ROOT, INSTALLED_HOME / ".local/share/shell-config",
                             "The installed check must run from the canonical runtime directory")
            for source_name, home_name in HOME_SOURCES:
                original = INSTALLED_HOME / home_name
                target = self.source / source_name
                self.assertTrue(original.is_file(), f"Missing exported public fixture file: {home_name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
        # The copied public tree is the complete fixture. Local/private state is
        # added only after the initial commit and cannot enter its allowlist.
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.state = self.root / "private migration state"
        self.env = {
            key: value for key, value in os.environ.items()
            if key not in {
                "ZDOTDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "PI_CODING_AGENT_DIR", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
                "SHELL_CONFIG_STATE_DIR", "DOTFILES_STATE_DIR",
            }
        }
        self.env.update({
            "HOME": str(self.home),
            "DOTFILES_STATE_DIR": str(self.state),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Dotfiles fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Dotfiles fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        })
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Dotfiles fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", os.devnull)
        self.git("remote", "add", "origin", "https://example.invalid/owner/shell-config.git")
        self.git("add", "--all")
        self.git("commit", "-m", "fixture public shell configuration")
        self.old_head = self.git("rev-parse", "HEAD").strip()

        self.old_zshrc = self.home / ".zshrc"
        self.old_zshrc.write_text("original local shell configuration\n")
        self.old_zshrc.chmod(0o600)
        config = self.home / ".config"
        config.mkdir()
        self.external_starship = self.root / "external starship.toml"
        self.external_starship.write_text("original external prompt\n")
        (config / "starship.toml").symlink_to(self.external_starship)
        agent = self.home / ".pi/agent"
        agent.mkdir(parents=True)
        (agent / "auth.json").write_text('{"credential":"local secret"}\n')
        (agent / "settings.json").write_text('{"theme":"local preference"}\n')
        self.original_home = tree_snapshot(self.home)

    def git(self, *args, bare=False):
        command = [GIT, "-C", str(self.home if bare else self.source)]
        if bare:
            command += [f"--git-dir={self.home / '.cfg'}", f"--work-tree={self.home}"]
        result = subprocess.run(command + list(args), env=self.env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def run_cli(self, *args, expected=0, env=None):
        result = subprocess.run(
            [sys.executable, str(CLI), *args], cwd=self.root,
            env={**self.env, **(env or {})}, input="", text=True,
            capture_output=True, timeout=20,
        )
        if expected is None:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout + result.stderr, r"outcome=[A-Z][A-Z_]+")
        else:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def migrate(self):
        return self.run_cli("migrate", "--source", str(self.source), "--yes")

    def test_migrate_preserves_history_source_and_private_boundaries(self):
        self.assertTrue(CLI.is_file(), "The requested bare Git migration entry point is absent")
        mise = self.source / "config/mise/config.toml"
        mise.write_text(mise.read_text() + "\n# reviewed local public change\n")
        (self.source / "pi/auth.json").write_text('{"credential":"never export"}\n')
        (self.source / ".zshrc.local").write_text("local secret\n")
        (self.source / "config/untracked-local.toml").write_text("private local data\n")
        index = (self.source / ".git/index").read_bytes()
        source_before = tree_snapshot(self.source, (Path(".git"),))
        self.migrate()
        self.assertEqual((self.source / ".git/index").read_bytes(), index)
        self.assertEqual(tree_snapshot(self.source, (Path(".git"),)), source_before)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old_head)
        self.assertEqual(self.git("rev-parse", "HEAD^", bare=True).strip(), self.old_head)
        self.assertEqual(self.git("remote", "get-url", "origin", bare=True).strip(),
                         "https://example.invalid/owner/shell-config.git")
        self.assertEqual(self.git("config", "--get", "status.showUntrackedFiles", bare=True).strip(), "no")
        self.assertEqual((self.home / ".config/mise/config.toml").read_bytes(), mise.read_bytes())

        tracked = set(self.git("ls-files", bare=True).splitlines())
        expected = {
            ".zshrc", ".zprofile", ".config/mise/config.toml", ".config/starship.toml",
            ".config/atuin/config.toml", ".config/ai/config.json", ".local/bin/ai",
            ".local/bin/config", ".local/bin/dotfiles", ".local/bin/shell-prompt",
            ".local/share/shell-config/ai/main.mjs",
            ".local/share/shell-config/prompt/context.mjs",
            ".local/share/shell-config/dotfiles.py",
            ".local/share/shell-config/tests/dotfiles.py",
            ".local/share/shell-config/pi/settings.public.json",
            ".local/share/shell-config/pi/packages.public.json",
            ".local/share/shell-config/pi/sync-settings.py",
            ".pi/agent/pi-lsp.json", ".pi/agent/agents/scout.md", ".pi/agent/prompts/verify.md",
        }
        self.assertTrue(expected <= tracked, expected - tracked)
        for relative in expected:
            path = self.home / relative
            self.assertTrue(path.is_file(), relative)
            self.assertFalse(path.is_symlink(), relative)
        self.assertNotIn(".zshpath", tracked)
        self.assertNotIn(".zshrc.local", tracked)
        self.assertNotIn(".pi/agent/auth.json", tracked)
        self.assertNotIn(".pi/agent/settings.json", tracked)
        self.assertNotIn(".config/untracked-local.toml", tracked)
        self.assertFalse((self.home / ".config/untracked-local.toml").exists())
        self.assertEqual((self.home / ".pi/agent/auth.json").read_text(), '{"credential":"local secret"}\n')
        self.assertEqual((self.home / ".pi/agent/settings.json").read_text(), '{"theme":"local preference"}\n')
        self.assertEqual(self.git("status", "--porcelain", bare=True), "")

    def test_plan_has_no_filesystem_or_git_effects(self):
        index = (self.source / ".git/index").read_bytes()
        source_before = tree_snapshot(self.source, (Path(".git"),))
        result = self.run_cli("plan", "--source", str(self.source))
        self.assertIn(".zshrc", result.stdout)
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertEqual(tree_snapshot(self.source, (Path(".git"),)), source_before)
        self.assertEqual((self.source / ".git/index").read_bytes(), index)
        self.assertFalse((self.home / ".cfg").exists())
        self.assertFalse(self.state.exists())

    def test_migration_captures_path_without_provisioning(self):
        tools = self.root / "tools with spaces"
        tools.mkdir()
        log = self.root / "provisioning was invoked"
        fake_mise = tools / "mise"
        fake_mise.write_text('#!/bin/sh\nprintf invoked > "$MISE_TEST_LOG"\nexit 37\n')
        fake_mise.chmod(0o755)
        original_path = f"{tools}{os.pathsep}{self.env['PATH']}"
        self.env.update({"PATH": original_path, "MISE_TEST_LOG": str(log)})
        self.migrate()
        captured = self.home / ".zshpath"
        self.assertEqual(captured.read_text(), original_path + "\n")
        self.assertEqual(stat.S_IMODE(captured.stat().st_mode), 0o600)
        self.assertFalse(captured.is_symlink())
        self.assertFalse(log.exists(), "Migration provisioned tools")

    def test_restore_preserves_modes_symlinks_and_original_absence(self):
        external_path = self.root / "external path capture"
        external_path.write_text("/local original path\n")
        external_path.chmod(0o600)
        path_capture = self.home / ".zshpath"
        path_capture.symlink_to(external_path)
        original = tree_snapshot(self.home)
        self.migrate()
        self.assertFalse(path_capture.is_symlink())
        self.assertEqual(path_capture.read_text(), "/local original path\n")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), original)
        self.assertFalse((self.home / ".cfg").exists())
        self.assertEqual(self.external_starship.read_text(), "original external prompt\n")
        self.assertEqual(external_path.read_text(), "/local original path\n")

    def test_restore_rejects_drift_before_mutating_any_managed_file(self):
        self.migrate()
        (self.home / ".zshrc").write_text("edit that must be preserved\n")
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertTrue((self.home / ".cfg").is_dir())

    def test_repeated_migration_preserves_home_edits_and_history(self):
        self.migrate()
        head = self.git("rev-parse", "HEAD", bare=True)
        (self.home / ".zshrc").write_text("local edit after migration\n")
        (self.source / ".zshrc").write_text("later source edit should not be re-exported\n")
        self.migrate()
        self.assertEqual((self.home / ".zshrc").read_text(), "local edit after migration\n")
        self.assertEqual(self.git("rev-parse", "HEAD", bare=True), head)
        status = self.run_cli("status")
        self.assertIn(".zshrc", status.stdout)

    def test_config_wrapper_quotes_paths_and_preserves_git_exit_status(self):
        self.migrate()
        wrapper = self.home / ".local/bin/config"
        relative = ".config/file with spaces; literal $.txt"
        target = self.home / relative
        target.write_text("selected public configuration\n")
        result = subprocess.run([str(wrapper), "add", "--force", "--", relative], cwd=self.root,
                                env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(relative, self.git("diff", "--cached", "--name-only", bare=True).splitlines())
        invalid_args = ["rev-parse", "--verify", "refs/heads/does-not-exist"]
        direct = subprocess.run(
            [GIT, "-C", str(self.home), f"--git-dir={self.home / '.cfg'}", f"--work-tree={self.home}", *invalid_args],
            env=self.env, text=True, capture_output=True, timeout=15,
        )
        wrapped = subprocess.run([str(wrapper), *invalid_args], cwd=self.root, env=self.env,
                                 text=True, capture_output=True, timeout=15)
        self.assertNotEqual(direct.returncode, 0)
        self.assertEqual(wrapped.returncode, direct.returncode)

        # Existing tracked files can be staged with ordinary native Git add.
        (self.home / ".zshrc").write_text("changed existing tracked file\n")
        tracked = subprocess.run([str(wrapper), "add", "--", ".zshrc"], cwd=self.root,
                                 env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        self.assertIn(".zshrc", self.git("diff", "--cached", "--name-only", bare=True).splitlines())

    def test_dotfiles_wrapper_works_in_checkout_and_installed_home(self):
        checkout = subprocess.run([str(CHECKOUT_WRAPPER), "plan", "--source", str(self.source)],
                                  cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(checkout.returncode, 0, checkout.stdout + checkout.stderr)
        self.migrate()
        installed = subprocess.run([str(self.home / ".local/bin/dotfiles"), "status"],
                                   cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)

    def test_nondefault_config_roots_are_rejected_without_changes(self):
        for variable in ("ZDOTDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "PI_CODING_AGENT_DIR"):
            with self.subTest(variable=variable):
                self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None,
                             env={variable: str(self.root / "unsupported root")})
                self.assertEqual(tree_snapshot(self.home), self.original_home)
                self.assertFalse(self.state.exists())

    def test_symlink_destination_directory_is_rejected_before_changes(self):
        config = self.home / ".config"
        external_config = self.root / "external config directory"
        config.rename(external_config)
        config.symlink_to(external_config, target_is_directory=True)
        before = tree_snapshot(self.home)
        external_before = tree_snapshot(external_config)
        self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home), before)
        self.assertEqual(tree_snapshot(external_config), external_before)
        self.assertFalse(self.state.exists())

    def test_existing_bare_repository_is_preserved_and_rejected(self):
        bare = self.home / ".cfg"
        result = subprocess.run([GIT, "init", "--bare", str(bare)], env=self.env,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = tree_snapshot(self.home)
        self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home), before)
        self.assertFalse(self.state.exists())

    def test_source_symlink_is_rejected_without_exporting_external_payload(self):
        external = self.root / "external private source"
        external.write_text("private source must not be exported\n")
        source_rc = self.source / ".zshrc"
        source_rc.unlink()
        source_rc.symlink_to(external)
        self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertFalse(self.state.exists())
        self.assertEqual(external.read_text(), "private source must not be exported\n")

    def test_source_symlink_ancestor_is_rejected_without_exporting_external_payload(self):
        external = self.root / "external private config directory"
        (self.source / "config").rename(external)
        (self.source / "config").symlink_to(external, target_is_directory=True)
        private = external / "mise/config.toml"
        private.write_text(private.read_text() + "\n# external private payload\n")
        external_before = tree_snapshot(external)
        self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertEqual(tree_snapshot(external), external_before)
        self.assertFalse(self.state.exists())

    def test_malformed_restore_metadata_is_rejected_before_home_changes(self):
        self.migrate()
        manifest = self.state / "manifest.json"
        manifest.write_text('{"type":"ACTIVE","home":42}\n')
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertTrue((self.home / ".cfg").is_dir())

    def test_missing_snapshot_item_is_rejected_before_home_changes(self):
        self.migrate()
        manifest = self.state / "manifest.json"
        payload = json.loads(manifest.read_text())
        payload["items"] = [item for item in payload["items"] if item["path"] != ".zshrc"]
        manifest.write_text(json.dumps(payload))
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertTrue((self.home / ".cfg").is_dir())

    def test_missing_imported_optional_item_is_rejected_before_home_changes(self):
        self.migrate()
        manifest = self.state / "manifest.json"
        payload = json.loads(manifest.read_text())
        path = ".pi/agent/agents/scout.md"
        self.assertIn(path, {item["path"] for item in payload["items"]})
        payload["items"] = [item for item in payload["items"] if item["path"] != path]
        manifest.write_text(json.dumps(payload))
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertTrue((self.home / ".cfg").is_dir())

    def test_missing_backup_file_is_rejected_before_home_changes(self):
        self.migrate()
        (self.state / "files/.zshrc").unlink()
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertTrue((self.home / ".cfg").is_dir())

    def test_staged_new_file_prevents_restore_and_preserves_all_work(self):
        self.migrate()
        added = self.home / ".config/added public file.toml"
        added.write_text("public change added after migration\n")
        self.git("add", "--force", "--", str(added), bare=True)
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertIn(".config/added public file.toml", self.git("diff", "--cached", "--name-only", bare=True))

    def test_committed_new_file_prevents_restore_and_preserves_history(self):
        self.migrate()
        added = self.home / ".config/added public file.toml"
        added.write_text("public change committed after migration\n")
        self.git("add", "--force", "--", str(added), bare=True)
        self.git("commit", "-m", "retain new dotfile history", bare=True)
        head = self.git("rev-parse", "HEAD", bare=True)
        before = tree_snapshot(self.home, (Path(".cfg"),))
        self.run_cli("restore", "--yes", expected=None)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), before)
        self.assertEqual(self.git("rev-parse", "HEAD", bare=True), head)

    def run_fault_driver(self, body, command, expected):
        """Inject one fault at an imported effect boundary, in a child process."""
        setup = """
import importlib.util
import os
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("dotfiles_fault_fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
home = Path(os.environ["HOME"])
state = Path(os.environ["DOTFILES_STATE_DIR"])
source = Path(sys.argv[2])
sys.argv = [sys.argv[1], COMMAND, "--source", str(source), "--yes"]
""".replace("COMMAND", repr(command))
        finish = """
try:
    result = module.main()
except KeyboardInterrupt:
    sys.exit(86)
except (OSError, UnicodeError):
    print("outcome=IO_FAILED")
    sys.exit(2)
if result:
    print("outcome=" + result.reason.value)
    sys.exit(2)
"""
        result = subprocess.run([sys.executable, "-c", setup + body + finish, str(CLI), str(self.source)],
                                cwd=self.root, env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_partial_migration_interruption_can_restore_exact_preimage(self):
        self.run_fault_driver("""
original_copy = module.copy_leaf
def interrupt_after_first_home_write(source_path, target):
    original_copy(source_path, target)
    if target == home / ".zshrc":
        raise KeyboardInterrupt()
module.copy_leaf = interrupt_after_first_home_write
""", "migrate", 86)
        self.assertNotEqual(self.old_zshrc.read_text(), "original local shell configuration\n")
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["type"], "PREPARED")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), self.original_home)

    def test_partial_restore_interruption_can_resume_exact_preimage(self):
        self.migrate()
        self.run_fault_driver("""
original_copy = module.copy_leaf
def interrupt_after_first_restore_write(source_path, target):
    original_copy(source_path, target)
    if target == home / ".zshrc":
        raise KeyboardInterrupt()
module.copy_leaf = interrupt_after_first_restore_write
""", "restore", 86)
        self.assertEqual(self.old_zshrc.read_text(), "original local shell configuration\n")
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["type"], "PREPARED")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), self.original_home)

    def test_partial_temporary_copy_interruption_keeps_migration_leaf_recoverable(self):
        self.run_fault_driver("""
original_copy = module.shutil.copy2
def interrupt_during_home_temporary_copy(source_path, target, *args, **kwargs):
    target = Path(target)
    if target.parent == home and target.name.startswith(".dotfiles-"):
        target.write_bytes(b"partial copy must never become the live leaf")
        raise KeyboardInterrupt()
    return original_copy(source_path, target, *args, **kwargs)
module.shutil.copy2 = interrupt_during_home_temporary_copy
""", "migrate", 86)
        self.assertEqual(self.old_zshrc.read_text(), "original local shell configuration\n")
        self.assertEqual(stat.S_IMODE(self.old_zshrc.stat().st_mode), 0o600)
        self.assertEqual(list(self.home.glob(".dotfiles-*")), [])
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["type"], "PREPARED")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), self.original_home)

    def test_partial_temporary_copy_interruption_keeps_restore_leaf_recoverable(self):
        self.migrate()
        migrated_rc = self.old_zshrc.read_bytes()
        self.run_fault_driver("""
original_copy = module.shutil.copy2
def interrupt_during_home_temporary_copy(source_path, target, *args, **kwargs):
    target = Path(target)
    if target.parent == home and target.name.startswith(".dotfiles-"):
        target.write_bytes(b"partial restore must never become the live leaf")
        raise KeyboardInterrupt()
    return original_copy(source_path, target, *args, **kwargs)
module.shutil.copy2 = interrupt_during_home_temporary_copy
""", "restore", 86)
        self.assertEqual(self.old_zshrc.read_bytes(), migrated_rc)
        self.assertEqual(list(self.home.glob(".dotfiles-*")), [])
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["type"], "PREPARED")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), self.original_home)

    def test_repository_owner_marker_failure_leaves_no_orphan_repository(self):
        self.run_fault_driver("""
original_open = Path.open
def fail_owner_marker(path, *args, **kwargs):
    if path == home / ".cfg/migration-owner":
        raise OSError("fixture owner-marker publication failure")
    return original_open(path, *args, **kwargs)
Path.open = fail_owner_marker
""", "migrate", 2)
        self.assertFalse((self.home / ".cfg").exists())
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["type"], "PREPARED")
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), self.original_home)

    def test_external_state_parent_symlink_into_source_is_rejected(self):
        alias = self.root / "external state directory alias"
        alias.symlink_to(self.source, target_is_directory=True)
        redirected_state = alias / "private migration state"
        source_before = tree_snapshot(self.source, (Path(".git"),))
        index_before = (self.source / ".git/index").read_bytes()
        self.run_cli("migrate", "--source", str(self.source), "--yes", expected=None,
                     env={"DOTFILES_STATE_DIR": str(redirected_state)})
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertEqual(tree_snapshot(self.source, (Path(".git"),)), source_before)
        self.assertEqual((self.source / ".git/index").read_bytes(), index_before)
        self.assertFalse(redirected_state.exists())

    def test_repository_appearing_after_plan_is_rejected_before_home_writes(self):
        self.run_fault_driver("""
original_build = module.build_repository
def build_then_create_competing_repository(*args, **kwargs):
    result = original_build(*args, **kwargs)
    if isinstance(result, module.Failure):
        raise AssertionError("Repository preparation failed before the fault injection")
    competing = home / ".cfg"
    competing.mkdir()
    (competing / "externally owned marker").write_text("preserve the competing repository\\n")
    return result
module.build_repository = build_then_create_competing_repository
""", "migrate", 2)
        self.assertEqual(tree_snapshot(self.home, (Path(".cfg"),)), self.original_home)
        self.assertEqual((self.home / ".cfg/externally owned marker").read_text(),
                         "preserve the competing repository\n")

    def test_legacy_cutover_marker_and_snapshots_survive_restore(self):
        legacy = self.home / ".local/state/shell-config-cutover"
        (legacy / "snapshot").mkdir(parents=True)
        (legacy / "snapshot/retained backup").write_text("legacy rollback material\n")
        (legacy / "active").touch()
        self.old_zshrc.unlink()
        self.old_zshrc.symlink_to(self.source / ".zshrc")
        before = tree_snapshot(self.home)
        self.migrate()
        self.assertFalse((legacy / "active").exists())
        self.assertEqual((legacy / "snapshot/retained backup").read_text(), "legacy rollback material\n")
        self.assertFalse(self.old_zshrc.is_symlink())
        self.run_cli("restore", "--yes")
        self.assertEqual(tree_snapshot(self.home), before)

    def test_headless_default_menu_fails_without_changing_state(self):
        self.run_cli(expected=None)
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertFalse(self.state.exists())

    def run_terminal(self, dialogue, *args):
        """Drive prompts through a real PTY, retaining a bounded transcript."""
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        command = [sys.executable, str(CLI), *args]
        process = subprocess.Popen(command, cwd=self.root, env=self.env,
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        def cleanup():
            if process.poll() is None:
                process.kill()
                process.wait()
        self.addCleanup(cleanup)
        transcript = bytearray()
        deadline = time.monotonic() + 20
        step = 0
        cursor = 0
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    transcript.extend(os.read(master, 65536))
                except OSError:
                    break
                self.assertLess(len(transcript), 1024 * 1024, "Unexpected unbounded terminal output")
                if step < len(dialogue):
                    prompt, response = dialogue[step]
                    found = transcript.find(prompt, cursor)
                    if found >= 0:
                        os.write(master, response)
                        cursor = found + len(prompt)
                        step += 1
        while select.select([master], [], [], 0)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            transcript.extend(chunk)
        self.assertEqual(step, len(dialogue), transcript.decode(errors="replace"))
        self.assertEqual(process.wait(timeout=2), 0, transcript.decode(errors="replace"))
        return transcript.decode(errors="replace")

    def test_terminal_menu_can_be_cancelled_without_changing_state(self):
        self.run_terminal([(b"Migrate these files now?", b"n\n")],
                          "menu", "--source", str(self.source))
        self.assertEqual(tree_snapshot(self.home), self.original_home)
        self.assertFalse(self.state.exists())

    def test_terminal_migration_wizard_requires_and_accepts_confirmation(self):
        self.run_terminal([(b"Migrate these files now?", b"y\n")],
                          "migrate", "--source", str(self.source))
        self.assertTrue((self.home / ".cfg").is_dir())
        self.assertEqual(self.git("rev-parse", "HEAD^", bare=True).strip(), self.old_head)
        self.assertFalse(self.old_zshrc.is_symlink())

    def test_active_terminal_menu_status_stage_and_commit_use_native_git(self):
        self.migrate()
        old_head = self.git("rev-parse", "HEAD", bare=True)
        self.git("config", "user.name", "Dotfiles fixture", bare=True)
        self.git("config", "user.email", "fixture@example.invalid", bare=True)
        self.git("config", "commit.gpgsign", "false", bare=True)
        self.old_zshrc.write_text(self.old_zshrc.read_text() + "\n# interactive public edit\n")
        starship = self.home / ".config/starship.toml"
        starship.write_text(starship.read_text() + "\n# leave this edit unstaged\n")
        transcript = self.run_terminal([
            (b"Choose:", b"1\n"),
            (b"Choose:", b"3\n"),
            (b"Numbers to stage", b"2\n"),
            (b"Choose:", b"4\n"),
            (b"Commit message", b"Record interactive public edit\n"),
            (b"Choose:", b"q\n"),
        ], "menu")
        self.assertIn(".zshrc", transcript)
        self.assertNotEqual(self.git("rev-parse", "HEAD", bare=True), old_head)
        self.assertEqual(self.git("log", "-1", "--format=%s", bare=True).strip(),
                         "Record interactive public edit")
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD", bare=True).splitlines(), [".zshrc"])
        self.assertEqual(self.git("diff", "--name-only", bare=True).splitlines(), [".config/starship.toml"])
        self.assertEqual(self.git("diff", "--cached", "--name-only", bare=True), "")


if __name__ == "__main__":
    unittest.main()

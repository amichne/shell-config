#!/usr/bin/env python3
"""Prove Pi shell changes against disposable HOME-shaped bare repositories."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "terminal/configure.py"
GIT = shutil.which("git")


class ConfigureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="configure tests with spaces ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.tools = self.root / "fixture tools"
        self.tools.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key not in {
                        "ZDOTDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                        "PI_CODING_AGENT_DIR", "SHELL_CONFIG_SESSIONS_DIR"}}
        self.env.update({"HOME": str(self.home),
                         "PATH": str(self.tools) + os.pathsep + self.env["PATH"],
                         "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                         "CONFIG_FIXTURE_MODE": "command", "REAL_GIT": GIT,
                         "CONFIG_FIXTURE_LOG": str(self.root / "pi invocation.json")})
        self.env["CONFIG_FIXTURE_CLI"] = str(CLI)
        self.git("init", "--bare", "-b", "main", str(self.home / ".cfg"), direct=True)
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", os.devnull),
                           ("status.showUntrackedFiles", "no")):
            self.git("config", key, value)
        self.write(".zshrc", "# public Zsh configuration\n")
        self.write(".zprofile", "# public login configuration\n")
        self.write(".config/starship.toml", 'add_newline = true\n')
        self.write(".config/atuin/config.toml", 'style = "compact"\n')
        self.write(".config/zsh/functions/existing", '# description: Existing helper\nprint -r -- existing\n')
        self.write(".local/bin/existing-command", '#!/bin/sh\n# description: Existing helper\nprintf "existing\\n"\n', 0o755)
        self.write(".gitignore", "*\n!*/\n")
        self.git("add", "--force", "--", ".zshrc", ".zprofile", ".config/starship.toml",
                 ".config/atuin/config.toml", ".config/zsh/functions/existing", ".local/bin/existing-command", ".gitignore")
        self.git("commit", "-m", "initial public configuration")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.write(".config/atuin/config.toml", 'style = "compact"\n# unrelated staged edit\n')
        self.git("add", "--", ".config/atuin/config.toml")
        self.unrelated_diff = self.git("diff", "--cached", "--binary")
        self.before_index = (self.home / ".cfg/index").read_bytes()
        self.pi = self.tools / "pi"
        self.pi.write_text(FAKE_PI)
        self.pi.chmod(0o755)

    def write(self, name, text, mode=0o644):
        target = self.home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(mode)
        return target

    def git(self, *args, direct=False):
        command = [GIT]
        if not direct:
            command += ["-C", str(self.home), f"--git-dir={self.home / '.cfg'}", f"--work-tree={self.home}"]
        result = subprocess.run(command + list(args), env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def run_cli(self, *args, success=True, mode=None):
        self.assertTrue(CLI.is_file(), "The Pi shell configuration transaction is absent")
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=self.root,
                                env={**self.env, **({"CONFIG_FIXTURE_MODE": mode} if mode else {})},
                                text=True, capture_output=True, timeout=25)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"stage=[a-z-]+ outcome=[A-Z_]+")
            self.assertNotIn("Traceback", result.stderr)
        return result

    def assert_untouched(self):
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual((self.home / ".cfg/index").read_bytes(), self.before_index)
        self.assertEqual((self.home / ".config/starship.toml").read_text(), 'add_newline = true\n')
        self.assertFalse((self.home / ".local/bin/fixture-helper").exists())

    def test_add_commits_only_candidate_and_preserves_unrelated_staged_edit(self):
        result = self.run_cli("add", "--name", "fixture-helper", "--kind", "command", "Make a helper")
        self.assertIn("outcome=COMMITTED", result.stdout)
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD").splitlines(),
                         [".local/bin/fixture-helper"])
        self.assertEqual(self.git("diff", "--cached", "--binary"), self.unrelated_diff)
        self.assertEqual(self.git("diff", "--name-only"), "")
        self.assertEqual(stat.S_IMODE((self.home / ".local/bin/fixture-helper").stat().st_mode), 0o755)
        invocation = json.loads((self.root / "pi invocation.json").read_text())
        self.assertNotEqual(Path(invocation["cwd"]), self.home)
        self.assertIn("--append-system-prompt", invocation["args"])
        self.assertIn("--name", invocation["args"])

    def test_function_is_autoloaded_and_smoke_checked(self):
        self.run_cli("add", "--name", "fixture-helper", "--kind", "function", "Make a function", mode="function")
        target = self.home / ".config/zsh/functions/fixture-helper"
        self.assertTrue(target.is_file())
        result = subprocess.run(["zsh", "-fc", 'fpath=("$1"); autoload -Uz fixture-helper; fixture-helper hello',
                                 "fixture", str(target.parent)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "hello\n")

    def test_change_updates_selected_general_configuration(self):
        self.run_cli("change", "Hide the blank prompt line", "--paths", ".config/starship.toml", mode="change")
        self.assertEqual((self.home / ".config/starship.toml").read_text(), 'add_newline = false\n')
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD").splitlines(), [".config/starship.toml"])
        self.assertEqual(self.git("diff", "--cached", "--binary"), self.unrelated_diff)

    def test_edit_resolves_an_existing_function(self):
        self.run_cli("edit", "existing", mode="edit")
        self.assertIn("updated", (self.home / ".config/zsh/functions/existing").read_text())
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD").splitlines(),
                         [".config/zsh/functions/existing"])

    def test_unnamed_add_lets_pi_choose_one_helper(self):
        self.run_cli("add", "Make a helper")
        self.assertTrue((self.home / ".local/bin/fixture-helper").is_file())

    def test_successful_pi_exit_requires_explicit_finish_intent(self):
        result = self.run_cli("add", "Make a helper", success=False, mode="no-proposal")
        self.assertIn("NO_FINISH_INTENT", result.stderr)
        self.assert_untouched()

    def test_failed_pi_run_cannot_commit_a_valid_proposal(self):
        result = self.run_cli("add", "Make a helper", success=False, mode="agent-failed")
        self.assertIn("AGENT_FAILED", result.stderr)
        self.assert_untouched()
        session = re.search(r"session=([0-9a-f]{32})", result.stdout).group(1)
        self.run_cli("finish", session, success=False)
        self.assert_untouched()

    def test_malformed_proposal_and_out_of_scope_private_path_fail_closed(self):
        for mode in ("extra-key", "private-path", "duplicate-path", "unknown-check"):
            with self.subTest(mode=mode):
                self.run_cli("add", "Make a helper", success=False, mode=mode)
                self.assert_untouched()

    def test_syntax_failure_and_special_candidate_permissions_do_not_install(self):
        for mode, reason in (("bad-syntax", "SYNTAX_FAILED"), ("special-mode", "INVALID_CANDIDATE"),
                             ("failed-smoke", "BEHAVIOR_FAILED"), ("missing-check", "INVALID_PROPOSAL")):
            with self.subTest(mode=mode):
                result = self.run_cli("add", "Make a helper", success=False, mode=mode)
                self.assertIn(reason, result.stderr)
                self.assert_untouched()

    def test_head_drift_and_authority_tampering_are_rejected(self):
        tamper = self.run_cli("add", "Make a helper", success=False, mode="tamper")
        self.assertIn("INVALID_SESSION", tamper.stderr)
        self.assert_untouched()
        result = self.run_cli("add", "Make a helper", success=False, mode="head-drift")
        self.assertIn("DRIFT", result.stderr)
        self.assertFalse((self.home / ".local/bin/fixture-helper").exists())
        self.assertEqual((self.home / ".cfg/index").read_bytes(), self.before_index)

    def test_preexisting_selected_edit_is_preserved_without_launching_pi(self):
        self.write(".config/starship.toml", 'add_newline = true\n# preexisting edit\n')
        self.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", success=False)
        self.assertFalse((self.root / "pi invocation.json").exists())
        self.assertIn("preexisting edit", (self.home / ".config/starship.toml").read_text())
        self.assertEqual((self.home / ".cfg/index").read_bytes(), self.before_index)

    def test_tool_name_collision_is_rejected_before_pi_launch(self):
        result = self.run_cli("add", "--name", "git", "Make a helper", success=False)
        self.assertIn("TOOL_COLLISION", result.stderr)
        self.assertFalse((self.root / "pi invocation.json").exists())
        self.assert_untouched()

    def test_add_cannot_recreate_a_preexisting_deleted_tracked_helper(self):
        target = self.home / ".local/bin/existing-command"
        target.unlink()
        result = self.run_cli("add", "--name", "existing-command", "--kind", "command", "Add helper", success=False)
        self.assertIn("PREEXISTING_EDIT", result.stderr)
        self.assertFalse((self.root / "pi invocation.json").exists())
        self.assertFalse(target.exists())
        self.assert_untouched()

    def test_symlink_ancestor_is_rejected(self):
        original = self.home / ".local/bin"
        external = self.root / "external commands"
        original.rename(external)
        original.symlink_to(external, target_is_directory=True)
        result = self.run_cli("add", "--name", "fixture-helper", "--kind", "command", "Make helper", success=False)
        self.assertIn("UNSAFE_PATH", result.stderr)
        self.assertFalse((external / "fixture-helper").exists())
        self.assertEqual((self.home / ".cfg/index").read_bytes(), self.before_index)

    def test_commit_failure_restores_exact_content_modes_index_and_absence(self):
        git_wrapper = self.tools / "git"
        git_wrapper.write_text('#!/usr/bin/env python3\nimport os, sys\n'
                               'if "commit-tree" in sys.argv: sys.exit(42)\n'
                               'os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *sys.argv[1:]])\n')
        git_wrapper.chmod(0o755)
        result = self.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", success=False, mode="change")
        self.assertIn("COMMIT_FAILED", result.stderr)
        self.assert_untouched()
        self.assertEqual(stat.S_IMODE((self.home / ".config/starship.toml").stat().st_mode), 0o644)
        self.run_cli("add", "Make helper", success=False)
        self.assert_untouched()

    def test_candidate_symlink_and_unlisted_changes_are_rejected(self):
        for mode in ("candidate-symlink", "unlisted-candidate"):
            with self.subTest(mode=mode):
                self.run_cli("add", "Make helper", success=False, mode=mode)
                self.assert_untouched()

    def test_malformed_json_shapes_are_finite_rejections(self):
        for mode in ("type-array", "check-type-array", "duplicate-json-key", "missing-metadata"):
            with self.subTest(mode=mode):
                self.run_cli("add", "Make helper", success=False, mode=mode)
                self.assert_untouched()

    def test_private_local_configuration_is_rejected_before_pi(self):
        self.run_cli("change", "Change local config", "--paths", ".config/nvim/config.local.json", success=False)
        self.assertFalse((self.root / "pi invocation.json").exists())
        self.assert_untouched()

    def test_default_change_refuses_overlap_with_preexisting_staging(self):
        result = self.run_cli("change", "Change my configuration", success=False)
        self.assertIn("PREEXISTING_EDIT", result.stderr)
        self.assertFalse((self.root / "pi invocation.json").exists())
        self.assert_untouched()

    def test_index_branch_and_live_preimage_drift_are_rejected(self):
        for mode in ("index-drift", "ref-drift", "live-drift"):
            with self.subTest(mode=mode):
                temporary = ConfigureTest()
                temporary.setUp()
                try:
                    result = temporary.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", success=False, mode=mode)
                    self.assertIn("DRIFT", result.stderr)
                    self.assertEqual(temporary.git("rev-parse", "HEAD").strip(), temporary.head)
                    self.assertFalse((temporary.home / ".local/bin/fixture-helper").exists())
                    if mode == "live-drift":
                        self.assertIn("external live edit", (temporary.home / ".config/starship.toml").read_text())
                    if mode == "index-drift":
                        self.assertIn("external staged edit", temporary.git("diff", "--cached"))
                finally:
                    temporary.doCleanups()

    def test_unlisted_candidate_deletion_is_rejected(self):
        self.run_cli("change", "Change settings", "--paths", ".config/starship.toml", ".zprofile",
                     success=False, mode="unlisted-deletion")
        self.assert_untouched()

    def test_interrupt_after_atomic_install_restores_the_preimage(self):
        driver = self.root / "fault driver.py"
        driver.write_text('import importlib.util, os, sys\nfrom pathlib import Path\n'
                          f'spec=importlib.util.spec_from_file_location("configure", {str(CLI)!r})\n'
                          'module=importlib.util.module_from_spec(spec); sys.modules["configure"]=module; spec.loader.exec_module(module)\n'
                          'original=module.atomic_write\n'
                          'def fail_after_install(target,data,mode):\n'
                          '    original(target,data,mode)\n'
                          '    if target == Path(os.environ["HOME"])/".config/starship.toml" and data == b"add_newline = false\\n":\n'
                          '        raise KeyboardInterrupt()\n'
                          'module.atomic_write=fail_after_install\n'
                          'sys.exit(module.main(sys.argv[1:]))\n')
        result = subprocess.run([sys.executable, str(driver), "change", "Change prompt", "--paths", ".config/starship.toml"],
                                cwd=self.root, env={**self.env, "CONFIG_FIXTURE_MODE": "change"}, text=True,
                                capture_output=True, timeout=25)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INTERRUPTED", result.stderr)
        self.assert_untouched()

    def test_configured_signing_failure_rolls_back(self):
        signer = self.tools / "failing signer"
        log = self.root / "signing attempted"
        signer.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n'
                          f'Path({str(log)!r}).write_text("attempted")\nsys.exit(42)\n')
        signer.chmod(0o755)
        self.git("config", "commit.gpgsign", "true")
        self.git("config", "user.signingkey", "fixture")
        self.git("config", "gpg.program", str(signer))
        result = self.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", mode="change", success=False)
        self.assertIn("COMMIT_FAILED", result.stderr)
        self.assertTrue(log.is_file(), "The configured signer was never invoked")
        self.assert_untouched()

    def test_help_does_not_inspect_or_create_home(self):
        nonexistent = self.root / "nonexistent home"
        result = subprocess.run([sys.executable, str(CLI), "--help"], env={**self.env, "HOME": str(nonexistent)},
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: config shell", result.stdout)
        self.assertFalse(nonexistent.exists())

    def test_finish_is_idempotent_after_a_commit(self):
        result = self.run_cli("add", "Make helper")
        session = re.search(r"session=([0-9a-f]{32})", result.stdout).group(1)
        committed = self.git("rev-parse", "HEAD")
        index = (self.home / ".cfg/index").read_bytes()
        repeated = self.run_cli("finish", session)
        self.assertIn("ALREADY_COMMITTED", repeated.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), committed)
        self.assertEqual((self.home / ".cfg/index").read_bytes(), index)

    def test_toml_and_python_syntax_checks_are_executed(self):
        result = self.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", mode="bad-toml", success=False)
        self.assertIn("SYNTAX_FAILED", result.stderr)
        self.assert_untouched()
        result = self.run_cli("add", "Make Python helper", mode="bad-python", success=False)
        self.assertIn("SYNTAX_FAILED", result.stderr)
        self.assert_untouched()
        self.run_cli("add", "Make Python helper", mode="python")
        self.assertEqual(self.git("diff", "--cached", "--binary"), self.unrelated_diff)

    def test_installed_config_dispatch_sigterm_rolls_back(self):
        for source_name, relative in (("bin/config", ".local/bin/config"),
                                      ("bin/shell-tools", ".local/bin/shell-tools"),
                                      ("terminal/main.py", ".local/share/shell-config/terminal/main.py"),
                                      ("terminal/configure.py", ".local/share/shell-config/terminal/configure.py"),
                                      ("pi/shell-context.md", ".local/share/shell-config/pi/shell-context.md")):
            source = ROOT / source_name
            if not source.is_file() and source_name.startswith("bin/"):
                source = ROOT.parents[2] / ".local" / source_name
            destination = self.home / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        git_wrapper = self.tools / "git"
        git_wrapper.write_text('#!/usr/bin/env python3\nimport os, signal, sys, time\n'
                               'if "commit-tree" in sys.argv:\n'
                               '    os.kill(os.getppid(), signal.SIGTERM)\n'
                               '    time.sleep(.1)\n'
                               '    sys.exit(42)\n'
                               'os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *sys.argv[1:]])\n')
        git_wrapper.chmod(0o755)
        result = subprocess.run([str(self.home / ".local/bin/config"), "shell", "change", "Change prompt",
                                 "--paths", ".config/starship.toml"], cwd=self.root,
                                env={**self.env, "CONFIG_FIXTURE_MODE": "change"}, text=True,
                                capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("stage=rollback outcome=RESTORED", result.stdout)
        self.assertIn("INTERRUPTED", result.stderr)
        self.assert_untouched()

    def test_imported_entry_restores_prior_signal_handlers(self):
        driver = self.root / "handler check.py"
        driver.write_text('import contextlib, importlib.util, io, signal, sys\n'
                          f'spec=importlib.util.spec_from_file_location("configure", {str(CLI)!r})\n'
                          'module=importlib.util.module_from_spec(spec); sys.modules["configure"]=module; spec.loader.exec_module(module)\n'
                          'before={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGHUP)}\n'
                          'with contextlib.redirect_stdout(io.StringIO()):\n'
                          '    try: module.main(["--help"])\n'
                          '    except SystemExit as e: assert e.code == 0\n'
                          'assert {s:signal.getsignal(s) for s in before} == before\n')
        result = subprocess.run([sys.executable, str(driver)], cwd=self.root, env=self.env,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_failure_after_index_lock_releases_it(self):
        driver = self.root / "index fault.py"
        driver.write_text('import importlib.util, os, sys\nfrom pathlib import Path\n'
                          f'spec=importlib.util.spec_from_file_location("configure", {str(CLI)!r})\n'
                          'module=importlib.util.module_from_spec(spec); sys.modules["configure"]=module; spec.loader.exec_module(module)\n'
                          'original=Path.read_bytes\n'
                          'def fail_index_read(path):\n'
                          '    if path == Path(os.environ["HOME"])/".cfg/index" and path.with_name("index.lock").exists():\n'
                          '        raise OSError("fixture index read failure")\n'
                          '    return original(path)\n'
                          'Path.read_bytes=fail_index_read\n'
                          'sys.exit(module.main(sys.argv[1:]))\n')
        result = subprocess.run([sys.executable, str(driver), "change", "Change prompt", "--paths", ".config/starship.toml"],
                                cwd=self.root, env={**self.env, "CONFIG_FIXTURE_MODE": "change"},
                                text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.home / ".cfg/index.lock").exists(), "An index read failure leaked Git's lock")
        self.assert_untouched()

    def test_mutated_private_preimage_cannot_be_reported_restored(self):
        signer = self.tools / "mutating signer"
        signer.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n'
                          'sessions=Path(os.environ["HOME"])/".local/state/shell-config/sessions"\n'
                          'for path in sessions.glob("*/preimages/.config/starship.toml"):\n'
                          '    path.chmod(0o600)\n'
                          '    path.write_text("corrupted = true\\n")\n'
                          'sys.exit(42)\n')
        signer.chmod(0o755)
        self.git("config", "commit.gpgsign", "true")
        self.git("config", "user.signingkey", "fixture")
        self.git("config", "gpg.program", str(signer))
        result = self.run_cli("change", "Change prompt", "--paths", ".config/starship.toml", mode="change", success=False)
        self.assertIn("RECOVERY_REQUIRED", result.stderr)
        self.assertNotIn("outcome=RESTORED", result.stdout)
        self.assertEqual((self.home / ".config/starship.toml").read_text(), 'add_newline = false\n')
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual((self.home / ".cfg/index").read_bytes(), self.before_index)
        self.assertFalse((self.home / ".cfg/index.lock").exists())

    def test_interruption_after_ref_publication_restores_owned_ref(self):
        driver = self.root / "ref fault.py"
        driver.write_text('import importlib.util, sys\n'
                          f'spec=importlib.util.spec_from_file_location("configure", {str(CLI)!r})\n'
                          'module=importlib.util.module_from_spec(spec); sys.modules["configure"]=module; spec.loader.exec_module(module)\n'
                          'original=module.git\ninjected=False\n'
                          'def interrupt_ref(home,*args,**kwargs):\n'
                          '    global injected\n'
                          '    result=original(home,*args,**kwargs)\n'
                          '    if not injected and args[0] == "update-ref" and isinstance(result,module.ProcessResult):\n'
                          '        injected=True\n'
                          '        raise KeyboardInterrupt()\n'
                          '    return result\n'
                          'module.git=interrupt_ref\n'
                          'sys.exit(module.main(sys.argv[1:]))\n')
        result = subprocess.run([sys.executable, str(driver), "change", "Change prompt", "--paths", ".config/starship.toml"],
                                cwd=self.root, env={**self.env, "CONFIG_FIXTURE_MODE": "change"},
                                text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("outcome=RESTORED", result.stdout)
        self.assertIn("INTERRUPTED", result.stderr)
        self.assert_untouched()

    def test_uncertain_index_publication_requires_recovery(self):
        driver = self.root / "index publication fault.py"
        driver.write_text('import importlib.util, os, sys\nfrom pathlib import Path\n'
                          f'spec=importlib.util.spec_from_file_location("configure", {str(CLI)!r})\n'
                          'module=importlib.util.module_from_spec(spec); sys.modules["configure"]=module; spec.loader.exec_module(module)\n'
                          'original=module.os.replace\n'
                          'def interrupt_index(source,target):\n'
                          '    original(source,target)\n'
                          '    if Path(target) == Path(os.environ["HOME"])/".cfg/index":\n'
                          '        raise KeyboardInterrupt()\n'
                          'module.os.replace=interrupt_index\n'
                          'sys.exit(module.main(sys.argv[1:]))\n')
        result = subprocess.run([sys.executable, str(driver), "change", "Change prompt", "--paths", ".config/starship.toml"],
                                cwd=self.root, env={**self.env, "CONFIG_FIXTURE_MODE": "change"},
                                text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("RECOVERY_REQUIRED", result.stderr)
        self.assertNotIn("outcome=RESTORED", result.stdout)
        self.assertEqual((self.home / ".config/starship.toml").read_text(), 'add_newline = false\n')
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.git("diff", "--cached", "--binary"), self.unrelated_diff)
        self.assertFalse((self.home / ".cfg/index.lock").exists())

    def test_public_finish_is_busy_while_pi_is_alive(self):
        self.run_cli("add", "Make helper", mode="premature-finish", success=False)
        result = json.loads((self.root / "child finish.json").read_text())
        self.assertEqual(result["code"], 2)
        self.assertIn("BUSY", result["stderr"])
        self.assert_untouched()


FAKE_PI = r'''#!/usr/bin/env python3
import json, os
from pathlib import Path
import subprocess, sys
root = Path.cwd()
Path(os.environ["CONFIG_FIXTURE_LOG"]).write_text(json.dumps({"cwd": str(root), "args": sys.argv[1:]}))
mode = os.environ["CONFIG_FIXTURE_MODE"]
if mode == "no-proposal": sys.exit(0)
if mode in ("change", "bad-toml", "index-drift", "ref-drift", "live-drift", "unlisted-deletion"):
    path = ".config/starship.toml"
    content = 'add_newline = false\n'
    checks = []
elif mode in ("function", "edit"):
    path = ".config/zsh/functions/" + ("existing" if mode == "edit" else "fixture-helper")
    content = '# description: Fixture helper\n# usage: fixture-helper [text]\nprint -r -- "' + ("updated" if mode == "edit" else "$1") + '"\n'
    checks = [{"type": "FUNCTION_SMOKE", "path": path, "args": ["hello"],
               "expect": {"type": "STDOUT", "code": 0, "contains": "updated" if mode == "edit" else "hello"}}]
else:
    path = ".local/bin/fixture-helper"
    content = '#!/bin/sh\n# description: Fixture helper\n# usage: fixture-helper --help\nprintf "fixture help\\n"\n'
    checks = [{"type": "COMMAND_SMOKE", "path": path, "args": ["--help"],
               "expect": {"type": "STDOUT", "code": 0, "contains": "fixture help"}}]
target = root / "candidates" / path
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(content)
target.chmod(0o755 if path.startswith(".local/bin/") else 0o644)
proposal = {"type": "CHANGESET", "summary": "Record fixture helper", "paths": [path], "checks": checks}
if mode == "bad-syntax": target.write_text(content + 'if then\n')
if mode == "bad-toml": target.write_text('add_newline = [\n')
if mode in ("python", "bad-python"):
    target.write_text('#!/usr/bin/env python3\n# description: Fixture helper\n# usage: fixture-helper --help\n'
                      + ('if :\n' if mode == "bad-python" else 'print("fixture help")\n'))
if mode == "special-mode": target.chmod(0o4755)
if mode == "extra-key": proposal["commit"] = True
if mode == "private-path": proposal["paths"] = [".pi/agent/auth.json"]
if mode == "duplicate-path": proposal["paths"].append(path)
if mode == "unknown-check": proposal["checks"][0]["type"] = "SHELL_COMMAND"
if mode == "failed-smoke": proposal["checks"][0]["expect"]["contains"] = "missing behavior"
if mode == "missing-check": proposal["checks"] = []
if mode == "type-array": proposal["type"] = []
if mode == "check-type-array": proposal["checks"][0]["type"] = []
if mode == "missing-metadata": target.write_text(content.replace('# usage: fixture-helper --help\n', ''))
if mode == "unlisted-deletion": (root / "candidates/.zprofile").unlink()
if mode == "candidate-symlink":
    target.unlink()
    target.symlink_to(Path(os.environ["HOME"]) / ".zshrc")
if mode == "unlisted-candidate":
    other = root / "candidates/.zshrc"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("# unrelated candidate\n")
if mode == "tamper":
    authority = root.parent / "authority.json"
    value = json.loads(authority.read_text())
    value["type"] = "COMMITTED"
    authority.chmod(0o600)
    authority.write_text(json.dumps(value))
if mode in ("head-drift", "index-drift", "ref-drift", "live-drift"):
    home = Path(os.environ["HOME"])
    base = [os.environ["REAL_GIT"], "-C", str(home), f"--git-dir={home / '.cfg'}", f"--work-tree={home}"]
    if mode == "head-drift":
        tree = subprocess.check_output(base + ["rev-parse", "HEAD^{tree}"]).decode().strip()
        old = subprocess.check_output(base + ["rev-parse", "HEAD"]).decode().strip()
        new = subprocess.check_output(base + ["commit-tree", tree, "-p", old, "-m", "unrelated external commit"]).decode().strip()
        subprocess.check_call(base + ["update-ref", "HEAD", new, old])
    if mode == "index-drift":
        changed = home / ".config/atuin/config.toml"
        changed.write_text(changed.read_text() + "# external staged edit\n")
        subprocess.check_call(base + ["add", "--", ".config/atuin/config.toml"])
    if mode == "ref-drift":
        subprocess.check_call(base + ["branch", "alternate"])
        subprocess.check_call(base + ["symbolic-ref", "HEAD", "refs/heads/alternate"])
    if mode == "live-drift":
        (home / ".config/starship.toml").write_text('add_newline = true\n# external live edit\n')
(root / "proposal.json").write_text(json.dumps(proposal))
if mode == "duplicate-json-key":
    (root / "proposal.json").write_text('{"type":"CHANGESET","type":"CHANGESET"}')
if mode == "premature-finish":
    session = json.loads((root / "request.json").read_text())["session"]
    receipt = Path(os.environ["HOME"]) / ".cfg/config-shell-authority" / (session + ".json")
    value = json.loads(receipt.read_text())
    value["type"] = "READY"
    receipt.write_text(json.dumps(value))
    child = subprocess.run([sys.executable, os.environ["CONFIG_FIXTURE_CLI"], "finish", session], text=True, capture_output=True)
    (Path(os.environ["CONFIG_FIXTURE_LOG"]).parent / "child finish.json").write_text(json.dumps({"code":child.returncode,"stderr":child.stderr}))
    sys.exit(3)
sys.exit(3 if mode == "agent-failed" else 0)
'''


if __name__ == "__main__":
    unittest.main()

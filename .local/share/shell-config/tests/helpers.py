#!/usr/bin/env python3
"""Public helper behavior with real shells and isolated homes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT/'bin' if (ROOT/'bin').is_dir() else Path.home()/'.local/bin'
FUNCTIONS = ROOT/'config/zsh/functions' if (ROOT/'config/zsh/functions').is_dir() else Path.home()/'.config/zsh/functions'


class HelpersTest(unittest.TestCase):
    def test_installed_runtime_is_independent_of_private_data_root(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            executable = root/'installed/bin'
            executable.mkdir(parents=True)
            for script in ('shell-tools', 'scratch'):
                shutil.copy2(BIN/script, executable/script)
            shutil.copytree(ROOT/'terminal', root/'installed/share/shell-config/terminal',
                            ignore=shutil.ignore_patterns('__pycache__'))
            data = root/'private-data'
            result = subprocess.run([str(executable/'scratch'), 'fixture'],
                                    env={**os.environ, 'XDG_DATA_HOME': str(data), 'VISUAL': '/usr/bin/true'},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((data/'shell-config/scratch/fixture.md').is_file())

    def test_shell_keys_work_without_terminal(self):
        cli = BIN / 'keys'
        self.assertTrue(cli.is_file(), 'The searchable shortcut command is missing')
        result = subprocess.run([str(cli), 'shell', '--list'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ctrl-R', result.stdout)
        self.assertIn('project', result.stdout)

    def test_yazi_changes_parent_directory_and_preserves_spaces(self):
        with tempfile.TemporaryDirectory(prefix='shell helpers ') as name:
            root = Path(name)
            destination = root / 'selected directory'
            destination.mkdir()
            fake = root / 'yazi'
            fake.write_text('#!/bin/sh\nfor arg do case "$arg" in --cwd-file=*) printf "%s" "$TARGET" > "${arg#--cwd-file=}";; esac; done\n')
            fake.chmod(0o755)
            result = subprocess.run(['zsh', '-f', '-c', 'fpath=("$FUNCTIONS" $fpath); autoload -Uz y; y; print -r -- "$PWD"'],
                                    env={**os.environ, 'PATH': f'{root}:/usr/bin:/bin', 'FUNCTIONS': str(FUNCTIONS), 'TARGET': str(destination)},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), destination.resolve())

    def test_failed_yazi_leaves_directory_and_exit_status(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fake = root / 'yazi'
            fake.write_text('#!/bin/sh\nexit 7\n')
            fake.chmod(0o755)
            result = subprocess.run(['zsh', '-f', '-c', 'fpath=("$FUNCTIONS" $fpath); autoload -Uz y; y; exit $?'],
                                    cwd=root, env={**os.environ, 'PATH': f'{root}:/usr/bin:/bin', 'FUNCTIONS': str(FUNCTIONS)}, capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr.decode())

    def test_project_selection_is_data_and_changes_shell_directory(self):
        with tempfile.TemporaryDirectory(prefix='project helpers ') as name:
            root = Path(name)
            destination = root/'literal $(do not execute)'
            destination.mkdir()
            fake = root/'project'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$TARGET"\n')
            fake.chmod(0o755)
            result = subprocess.run(['zsh', '-f', '-c', 'fpath=("$FUNCTIONS" $fpath); autoload -Uz p; p; print -r -- "$PWD"'],
                                    env={**os.environ, 'PATH': f'{root}:/usr/bin:/bin', 'FUNCTIONS': str(FUNCTIONS), 'TARGET': str(destination)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), destination.resolve())

    def test_invalid_scratch_name_cannot_escape_directory(self):
        cli = BIN/'scratch'
        self.assertTrue(cli.is_file(), 'Scratch command is missing')
        result = subprocess.run([str(cli), '../escape'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('INVALID_NAME', result.stderr)

    def test_scratch_restores_private_permissions_and_rejects_bad_editor(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            note = root/'data/shell-config/scratch/notes.md'
            note.parent.mkdir(parents=True)
            note.write_text('private note\n')
            note.chmod(0o644)
            env = {**os.environ, 'XDG_DATA_HOME': str(root/'data'), 'VISUAL': '/usr/bin/true'}
            result = subprocess.run([str(BIN/'scratch')], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(note.stat().st_mode & 0o777, 0o600)
            result = subprocess.run([str(BIN/'scratch')], env={**env, 'VISUAL': "'unterminated"}, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('INVALID_EDITOR', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_keys_uses_the_shells_config_directory(self):
        with tempfile.TemporaryDirectory() as name:
            config = Path(name)/'config'
            function = config/'zsh/functions/xdg-helper'
            function.parent.mkdir(parents=True)
            function.write_text('# description: Fixture relocated helper\nprint fixture\n')
            result = subprocess.run([str(BIN/'keys'), 'shell', '--list'], env={**os.environ, 'XDG_CONFIG_HOME': str(config)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Fixture relocated helper', result.stdout)


if __name__ == '__main__':
    unittest.main()

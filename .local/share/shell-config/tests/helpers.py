#!/usr/bin/env python3
"""Public helper behavior with real shells and isolated homes."""
import importlib.util
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_HOME = ROOT if (ROOT/'.zshrc').is_file() else ROOT.parents[2]
BIN = ROOT/'bin' if (ROOT/'bin').is_dir() else PUBLIC_HOME/'.local/bin'
FUNCTIONS = ROOT/'config/zsh/functions' if (ROOT/'config/zsh/functions').is_dir() else PUBLIC_HOME/'.config/zsh/functions'
ZSHRC = PUBLIC_HOME/'.zshrc'
CONFIG_HANDLER = ROOT/'config/zsh/completions/_config' if (ROOT/'config/zsh/completions/_config').is_file() else PUBLIC_HOME/'.config/zsh/completions/_config'
spec = importlib.util.spec_from_file_location('helper_completion_probe', ROOT/'terminal/configure.py')
configure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = configure
spec.loader.exec_module(configure)


class HelpersTest(unittest.TestCase):
    def test_ctrl_w_deletes_word_parts_with_autosuggestions_and_preserves_editing(self):
        # Exercise the configured key in native ZLE with the real system plugins.
        # Integration commands are inert so this fixture never opens history,
        # authenticates, or reads machine configuration.
        cases = (
            ('camel', 'parseHTTPResponse', 17, b'\x17', 'parseHTTP'),
            ('acronym', 'parseHTTP', 9, b'\x17', 'parse'),
            ('snake', 'snake_case', 10, b'\x17', 'snake_'),
            ('path', 'path/to/file', 12, b'\x17', 'path/to/'),
            ('dash', 'some-name', 9, b'\x17', 'some-'),
            ('dot', 'file.ext', 8, b'\x17', 'file.'),
            ('space', 'one two', 7, b'\x17', 'one '),
            ('trailing space', 'one two  ', 9, b'\x17', 'one '),
            ('middle', 'one two tail', 7, b'\x17', 'one  tail'),
            ('start', 'one two', 0, b'\x17', 'one two'),
            ('empty', '', 0, b'\x17', ''),
            ('numeric', 'parseHTTPResponse', 17, b'\x1b2\x17', 'parse'),
            ('negative numeric', 'one two', 0, b'\x1b-\x17', ' two'),
            ('yank accumulated kills', 'parseHTTPResponse', 17, b'\x17\x17\x19', 'parseHTTPResponse'),
            ('undo', 'snake_case', 10, b'\x17\x1f', 'snake_case'),
        )
        with tempfile.TemporaryDirectory(prefix='shell editing with spaces ') as name:
            root = Path(name)
            tools = root/'tools'
            tools.mkdir()
            for command in ('mise', 'atuin', 'zoxide', 'wt', 'fzf', 'starship'):
                executable = tools/command
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
            capture = root/'editing capture'
            seed_buffers = ' '.join(shlex.quote(case[1]) for case in cases)
            seed_cursors = ' '.join(str(case[2]) for case in cases)
            script = root/'editing.zsh'
            script.write_text(f'''
source {shlex.quote(str(ZSHRC))} || exit 91
source {shlex.quote(str(ZSHRC))} || exit 92
(( $+functions[_zsh_autosuggest_start] )) || exit 93
_zsh_autosuggest_start
[[ $widgets[backward-kill-word] == user:_zsh_autosuggest_bound_* ]] || exit 94
PROMPT=''; RPROMPT=''
typeset -a _edit_buffers=({seed_buffers}) _edit_cursors=({seed_cursors})
typeset -gi _edit_case=0
_edit_seed() {{
    (( ++_edit_case ))
    BUFFER=$_edit_buffers[_edit_case]
    CURSOR=$_edit_cursors[_edit_case]
    CUTBUFFER='previous kill'
    POSTDISPLAY=' synthetic suggestion'
    builtin print -r -- "SC_EDIT_READY_$_edit_case"
    zle redisplay
}}
_edit_capture() {{
    builtin print -rN -- "$BUFFER" > {shlex.quote(str(capture))}
    builtin print -r -- "SC_EDIT_DONE_$_edit_case"
    BUFFER=''; CURSOR=0
    zle redisplay
}}
zle -N _edit_seed
zle -N _edit_capture
bindkey '^X' _edit_seed
bindkey '^]' _edit_capture
builtin print -r -- SC_EDIT_BOOT
''')
            env = {'HOME': str(root), 'ZDOTDIR': str(root), 'PATH': f'{tools}:/opt/homebrew/bin:/usr/bin:/bin',
                   'XDG_CONFIG_HOME': str(root/'.config'), 'XDG_DATA_HOME': str(root/'.local/share'),
                   'XDG_STATE_HOME': str(root/'.local/state'), 'TERM': 'xterm-256color', 'LC_ALL': 'C'}
            pid, terminal = pty.fork()
            if pid == 0:
                os.chdir(root)
                os.execve('/bin/zsh', ['/bin/zsh', '-d', '-f', '-i'], env)
            transcript = bytearray()
            def wait_for(marker):
                deadline = time.monotonic() + 8
                while marker not in transcript and time.monotonic() < deadline:
                    if select.select([terminal], [], [], 0.1)[0]:
                        try:
                            data = os.read(terminal, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        transcript.extend(data)
                self.assertIn(marker, transcript, transcript.decode(errors='replace'))
            try:
                os.write(terminal, ('source '+shlex.quote(str(script))+'\n').encode())
                wait_for(b'SC_EDIT_BOOT')
                for index, (label, _, _, keys, expected) in enumerate(cases, 1):
                    with self.subTest(case=label):
                        os.write(terminal, b'\x18')
                        wait_for(f'SC_EDIT_READY_{index}\r\n'.encode())
                        os.write(terminal, keys+b'\x1d')
                        wait_for(f'SC_EDIT_DONE_{index}\r\n'.encode())
                        self.assertEqual(capture.read_bytes(), expected.encode()+b'\0', label)
            finally:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(terminal)
                os.waitpid(pid, 0)

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


CONFIG_COMPLETION_SETUP=r'''
command git init -q --bare "$HOME/.cfg" || exit 93
builtin print fixture > "$HOME/home-file.txt"
command git -C "$HOME" --git-dir="$HOME/.cfg" --work-tree="$HOME" add home-file.txt || exit 93
command git -C "$HOME" --git-dir="$HOME/.cfg" --work-tree="$HOME" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm fixture || exit 93
command git -C "$HOME" --git-dir="$HOME/.cfg" --work-tree="$HOME" branch home-only || exit 93
command git init -q "$HOME/nested repo $(literal)" || exit 93
builtin cd -q -- "$HOME/nested repo $(literal)" || exit 93
builtin print fixture > cwd-file.txt
command git add cwd-file.txt || exit 93
command git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm fixture || exit 93
command git branch cwd-only || exit 93
SC_FIXTURE_PWD=$PWD
SC_FIXTURE_OLDPWD=$OLDPWD
SC_FIXTURE_DIRSTACK="${(j:,:)dirstack}"
'''
# literal `$(literal)` is quoted in the shell fixture name, so it remains data.
CONFIG_COMPLETION_SETUP=CONFIG_COMPLETION_SETUP.replace('$(literal)', r'\$(literal)')

class ConfigCompletionTest(unittest.TestCase):
    def matches(self,args,prefix,buffer=None):
        setup=CONFIG_COMPLETION_SETUP+ ('\nSC_PROBE_BUFFER='+shlex.quote(buffer)+'\n' if buffer else '')
        script=configure.COMPLETION_PROBE_SCRIPT.replace('unsetopt beep', 'unsetopt beep\n'+setup)
        script=script.replace('    zle complete-word', '    zle complete-word\n    [[ $PWD == "$SC_FIXTURE_PWD" && $OLDPWD == "$SC_FIXTURE_OLDPWD" && "${(j:,:)dirstack}" == "$SC_FIXTURE_DIRSTACK" ]] || return 97')
        with patch.object(configure,'COMPLETION_PROBE_SCRIPT',script):
            result=configure.probe_completion(CONFIG_HANDLER,'config',args,prefix)
        self.assertIsInstance(result,configure.CompletionMatches)
        return tuple(candidate.rstrip() for candidate in result.candidates)

    def test_config_delegates_git_completion_and_keeps_shell_workflows(self):
        for args,prefix,expected in [((), 'sta', 'status'), (('status',), '--por', '--porcelain'), ((), 'she', 'shell'), (('shell',), 'ad', 'add'), (('shell',), 'ed', 'edit'), (('shell',), 'co', 'completion'), (('shell',), 'ch', 'change'), (('shell',), 'fi', 'finish'), (('shell','completion'),'gi','git')]:
            with self.subTest(args=args,prefix=prefix):
                self.assertIn(expected,self.matches(args,prefix))

    def test_config_queries_home_bare_repo_but_git_queries_cwd_repo(self):
        self.assertIn('home-only',self.matches(('checkout',),'home-'))
        self.assertNotIn('cwd-only',self.matches(('checkout',),'cwd-'))
        self.assertIn('cwd-only',self.matches((),'',buffer='git checkout cwd-'))
        self.assertNotIn('home-only',self.matches((),'',buffer='git checkout home-'))

    def test_config_path_matches_home_but_git_path_matches_cwd(self):
        self.assertIn('home-file.txt',self.matches(('add',),'home-'))
        self.assertNotIn('cwd-file.txt',self.matches(('add',),'cwd-'))
        self.assertIn('cwd-file.txt',self.matches((),'',buffer='git add cwd-'))
        self.assertNotIn('home-file.txt',self.matches((),'',buffer='git add home-'))


if __name__ == '__main__':
    unittest.main()

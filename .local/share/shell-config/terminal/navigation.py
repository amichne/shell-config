"""Project selection emits paths as data; only Zsh performs directory handoff."""
from dataclasses import dataclass
from enum import Enum
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile


class Reason(Enum):
    INVALID_NAME = 'INVALID_NAME'
    INVALID_EDITOR = 'INVALID_EDITOR'
    UNSAFE_PATH = 'UNSAFE_PATH'
    COMMAND_FAILED = 'COMMAND_FAILED'
    MISSING_COMMAND = 'MISSING_COMMAND'
    INVALID_SELECTION = 'INVALID_SELECTION'
    NO_PROJECTS = 'NO_PROJECTS'
    IO_FAILED = 'IO_FAILED'


@dataclass(frozen=True)
class Failure:
    reason: Reason


@dataclass(frozen=True)
class Project:
    path: Path


def git_env():
    return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}


def projects(home: Path) -> tuple[Project, ...] | Failure:
    # One-level discovery under explicit code roots, plus actual Git worktrees.
    roots = [Path(p).expanduser() for p in os.environ.get('PROJECT_ROOTS', str(home/'code')).split(os.pathsep) if p]
    candidates = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.is_dir() and (path/'.git').exists():
                candidates.add(path.resolve())
                result = subprocess.run(['git', '-C', str(path), 'worktree', 'list', '--porcelain', '-z'], env=git_env(), capture_output=True, timeout=10)
                if result.returncode:
                    return Failure(Reason.COMMAND_FAILED)
                for field in result.stdout.split(b'\0'):
                    if field.startswith(b'worktree '):
                        tree = Path(os.fsdecode(field[9:]))
                        if tree.is_dir():
                            candidates.add(tree.resolve())
    if any(any(ord(c) < 32 or ord(c) == 127 for c in str(path)) for path in candidates):
        return Failure(Reason.UNSAFE_PATH)
    return tuple(Project(p) for p in sorted(candidates))


def pick(values: tuple[Project, ...], query: str) -> Path | Failure:
    if not values:
        return Failure(Reason.NO_PROJECTS)
    rows = [str(p.path) for p in values]
    result = subprocess.run(['fzf', '--height=60%', '--layout=reverse', '--border', '--prompt=project> ', '--header=Enter: jump   Esc: cancel', '--query', query], input='\n'.join(rows)+'\n', text=True, stdout=subprocess.PIPE)
    if result.returncode in (1, 130):
        return Failure(Reason.INVALID_SELECTION)
    if result.returncode or result.stdout.rstrip('\n') not in rows:
        return Failure(Reason.INVALID_SELECTION)
    return Path(result.stdout.rstrip('\n'))


def scratch(home: Path, name: str, temporary: bool) -> int | Failure:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name) or name in ('.', '..'):
        return Failure(Reason.INVALID_NAME)
    try:
        editor = shlex.split(os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'nvim')
    except ValueError:
        return Failure(Reason.INVALID_EDITOR)
    if not editor:
        return Failure(Reason.MISSING_COMMAND)
    if temporary:
        with tempfile.TemporaryDirectory(prefix='scratch-') as directory:
            return subprocess.run([*editor, '--', str(Path(directory)/(name+'.md'))]).returncode
    directory = Path(os.environ.get('XDG_DATA_HOME', str(home/'.local/share')))/'shell-config/scratch'
    target = directory/(name+'.md')
    if any(p.is_symlink() for p in (target, directory, *directory.parents)):
        return Failure(Reason.UNSAFE_PATH)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        file = os.fstat(fd)
        if not stat.S_ISREG(file.st_mode) or file.st_nlink != 1:
            return Failure(Reason.UNSAFE_PATH)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return subprocess.run([*editor, '--', str(target)]).returncode


def main(argv: list[str]) -> int:
    action, *options = argv
    parser = argparse.ArgumentParser(prog=action, description=__doc__)
    if action == 'project':
        parser.add_argument('query', nargs='*')
        parser.add_argument('--list', action='store_true')
        parser.add_argument('--select', action='store_true')
        parser.add_argument('--json', action='store_true')
    else:
        parser.add_argument('name', nargs='?', default='notes')
        parser.add_argument('--temp', action='store_true')
    args = parser.parse_args(options)
    args.command = action
    try:
        if args.command == 'scratch':
            outcome = scratch(Path.home(), args.name, args.temp)
        else:
            outcome = projects(Path.home())
            if not isinstance(outcome, Failure):
                if args.list or args.json or (not args.select and not sys.stdin.isatty()):
                    if args.json:
                        print(json.dumps([{'type': 'PROJECT', 'path': str(p.path)} for p in outcome]))
                    else:
                        for entry in outcome:
                            print(entry.path)
                    return 0
                outcome = pick(outcome, ' '.join(args.query))
                if isinstance(outcome, Path):
                    print(outcome)
                    return 0
        if isinstance(outcome, Failure):
            print(f'{args.command}: stage={args.command} outcome={outcome.reason.value}', file=sys.stderr)
            return 2
        return outcome
    except FileNotFoundError:
        print(f'{args.command}: stage=process outcome=MISSING_COMMAND', file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print(f'{args.command}: stage=process outcome=COMMAND_FAILED', file=sys.stderr)
        return 2
    except OSError:
        print(f'{args.command}: stage=filesystem outcome=IO_FAILED', file=sys.stderr)
        return 2

"""Searchable starter shortcuts, plus descriptions from installed custom maps."""
from dataclasses import dataclass
from enum import Enum
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


@dataclass(frozen=True)
class Shortcut:
    tool: str
    key: str
    description: str
    example: str = ''


class Reason(Enum):
    NVIM_CONFIG_FAILED = 'NVIM_CONFIG_FAILED'
    INVALID_MAPS = 'INVALID_MAPS'
    INVALID_SELECTION = 'INVALID_SELECTION'
    MISSING_COMMAND = 'MISSING_COMMAND'
    IO_FAILED = 'IO_FAILED'
    TIMED_OUT = 'TIMED_OUT'


@dataclass(frozen=True)
class Failure:
    reason: Reason


STARTERS = (
    Shortcut('shell', 'Ctrl-R', 'Search Atuin history; select a command before executing'),
    Shortcut('shell', 'Ctrl-T', 'Select a filename into the command line'),
    Shortcut('shell', 'Alt-C', 'Select and enter a directory'),
    Shortcut('shell', '** Tab', 'Fuzzy path completion', 'nvim **<Tab>'),
    Shortcut('shell', 'Up / Down', 'Search history using the prefix already typed'),
    Shortcut('shell', 'Right arrow', 'Accept a history suggestion at the end of the line'),
    Shortcut('shell', 'Ctrl-X ?', 'Open this shortcut browser'),
    Shortcut('shell', 'main', 'Enter the default-branch worktree'),
    Shortcut('shell', 'rr', 'Enter the current repository root'),
    Shortcut('shell', 'project / p', 'Pick a repository or worktree; p enters it'),
    Shortcut('shell', 'config shell add', 'Create, validate, install and commit a helper with Pi'),
    Shortcut('shell', 'config shell change', 'Change public configuration with Pi'),
    Shortcut('yazi', 'F1 / ~', 'Show full contextual help'),
    Shortcut('yazi', 's', 'Search filenames with fd'),
    Shortcut('yazi', 'S', 'Search contents with ripgrep'),
    Shortcut('yazi', 'z', 'Jump with fzf'),
    Shortcut('yazi', 'Z', 'Jump with zoxide'),
    Shortcut('yazi', 'Space', 'Toggle selection'),
    Shortcut('yazi', 'y / p', 'Copy selection / paste selection'),
    Shortcut('yazi', 'q', 'Quit and hand the directory back to the shell'),
    Shortcut('yazi', 'Q', 'Quit while keeping the shell directory'),
)


def custom_helpers(home: Path) -> list[Shortcut]:
    values = []
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(home/'.config')))
    for directory in (config/'zsh/functions', home/'.local/bin'):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as source:
                    lines = source.read(4096).decode('utf-8', errors='replace').splitlines()
                description = next((s.removeprefix('# description:').strip() for s in lines if s.startswith('# description:')), '')
                usage = next((s.removeprefix('# usage:').strip() for s in lines if s.startswith('# usage:')), path.name)
                if description:
                    values.append(Shortcut('shell', path.name, description, usage))
    return values


def nvim_shortcuts() -> list[Shortcut] | Failure:
    script = 'lua local maps={}; for _,m in ipairs(vim.api.nvim_get_keymap("n")) do if m.desc then table.insert(maps,{key=m.lhs,description=m.desc}) end end; io.stdout:write(vim.json.encode(maps))'
    result = subprocess.run(['nvim', '--headless', '-c', script, '-c', 'qa'], capture_output=True, text=True, timeout=20)
    if result.returncode:
        return Failure(Reason.NVIM_CONFIG_FAILED)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return Failure(Reason.INVALID_MAPS)
    if not isinstance(raw, list) or len(raw) > 1000:
        return Failure(Reason.INVALID_MAPS)
    values = []
    for row in raw:
        if not isinstance(row, dict) or set(row) != {'key', 'description'} or any(not isinstance(s, str) for s in row.values()):
            return Failure(Reason.INVALID_MAPS)
        values.append(Shortcut('nvim', row['key'].replace(' ', 'Space ', 1) if row['key'].startswith(' ') else row['key'], row['description']))
    return sorted(values, key=lambda s: s.key)


def safe_text(value: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='keys', description=__doc__)
    parser.add_argument('tool', nargs='?', choices=('shell', 'nvim', 'yazi', 'work'))
    parser.add_argument('--list', action='store_true', help='Print without a terminal picker')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        values = [s for s in STARTERS if args.tool is None or s.tool == args.tool]
        if args.tool in (None, 'shell'):
            values += custom_helpers(Path.home())
        if args.tool == 'nvim':
            maps = nvim_shortcuts()
            if isinstance(maps, Failure):
                return report(maps)
            values += maps
        elif args.tool in (None, 'work'):
            import work
            values += [Shortcut('work', s['key'], s['description'], s.get('example', '')) for s in work.shortcuts()]
        if args.json:
            print(json.dumps([{'type': 'SHORTCUT', 'tool': s.tool, 'key': s.key, 'description': s.description, 'example': s.example} for s in values]))
            return 0
        rows = ['\t'.join(safe_text(s) for s in (v.tool, v.key, v.description, v.example)) for v in values]
        if args.list or not sys.stdin.isatty():
            print('\n'.join(rows))
            return 0
        result = subprocess.run(['fzf', '--height=70%', '--layout=reverse', '--border', '--delimiter=\t', '--with-nth=1,2,3,4', '--prompt=keys> ', '--header=Search actions and examples; Enter closes, Esc cancels'], input='\n'.join(rows)+'\n', text=True, stdout=subprocess.PIPE)
        if result.returncode in (1, 130):
            return 0
        if result.returncode or result.stdout.rstrip('\n') not in rows:
            return report(Failure(Reason.INVALID_SELECTION))
        print(result.stdout.rstrip('\n'))
        return 0
    except FileNotFoundError:
        return report(Failure(Reason.MISSING_COMMAND))
    except OSError:
        return report(Failure(Reason.IO_FAILED))
    except subprocess.TimeoutExpired:
        return report(Failure(Reason.TIMED_OUT))


def report(failure: Failure) -> int:
    print(f'keys: stage=shortcuts outcome={failure.reason.value}; use the tool\'s native help', file=sys.stderr)
    return 127 if failure.reason is Reason.MISSING_COMMAND else 2

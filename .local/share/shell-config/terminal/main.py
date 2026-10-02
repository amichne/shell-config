#!/usr/bin/env python3
"""Installed shell helpers: each command owns its effects and finite outcomes."""
import sys


def main():
    if len(sys.argv) < 2:
        print('usage: shell-tools keys|project|scratch|work|configure', file=sys.stderr)
        return 2
    action, *args = sys.argv[1:]
    match action:
        case 'keys':
            import keys
            return keys.main(args)
        case 'project' | 'scratch':
            import navigation
            return navigation.main([action, *args])
        case 'work':
            import work
            return work.main(args)
        case 'configure':
            import configure
            return configure.main(args)
        case _:
            print('shell-tools: stage=dispatch outcome=UNKNOWN_COMMAND', file=sys.stderr)
            return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

#!/usr/bin/env python3
"""Preview or apply repo-owned Pi preferences without replacing local settings."""
import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

PUBLIC = Path(__file__).with_name("settings.public.json")
PACKAGES = Path(__file__).with_name("packages.public.json")


def source_identity(entry):
    source = entry if isinstance(entry, str) else entry.get("source")
    if not isinstance(source, str):
        raise ValueError("package source must be a string")
    if source.startswith("git:"):
        return source.split("@", 1)[0]
    if source.startswith("npm:") and source.rfind("@") > source.rfind("/"):
        return source[: source.rfind("@")]
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path.home() / ".pi/agent/settings.json")
    parser.add_argument("--apply", action="store_true", help="write settings (default: preview only)")
    args = parser.parse_args()

    try:
        if args.target.is_symlink() or not args.target.is_file():
            raise ValueError("target must be an existing regular file, not a symlink")
        current = json.loads(args.target.read_text())
        public = json.loads(PUBLIC.read_text())
        packages = json.loads(PACKAGES.read_text())
        if not isinstance(current, dict) or not isinstance(public, dict):
            raise ValueError("settings must be JSON objects")
        if not isinstance(packages, list) or not isinstance(current.get("packages", []), list):
            raise ValueError("packages must be JSON arrays")
        managed = {source_identity(entry) for entry in packages}
        retained = [entry for entry in current.get("packages", []) if source_identity(entry) not in managed]
        # Do not print machine-local settings, even during preview.
        print(json.dumps({**public, "packages": packages}, indent=2))
        if not args.apply:
            return 0
        mode = stat.S_IMODE(args.target.stat().st_mode)
        merged = {**current, **public, "packages": retained + packages}
        fd, temp_name = tempfile.mkstemp(prefix=".settings-", dir=args.target.parent)
        try:
            with os.fdopen(fd, "w") as output:
                os.fchmod(output.fileno(), mode)
                json.dump(merged, output, indent=2)
                output.write("\n")
            os.replace(temp_name, args.target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"pi settings: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

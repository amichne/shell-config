#!/usr/bin/env python3
"""One-shot conversion to a bare Git repository with HOME as its worktree."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile


class Stage(Enum):
    PREFLIGHT = "preflight"
    REPOSITORY = "repository"
    SNAPSHOT = "snapshot"
    CHECKOUT = "checkout"
    RESTORE = "restore"
    MENU = "menu"


class Reason(Enum):
    INVALID_LAYOUT = "INVALID_LAYOUT"
    UNSUPPORTED_ROOT = "UNSUPPORTED_ROOT"
    UNSAFE_PATH = "UNSAFE_PATH"
    REPOSITORY_EXISTS = "REPOSITORY_EXISTS"
    GIT_FAILED = "GIT_FAILED"
    INVALID_SNAPSHOT = "INVALID_SNAPSHOT"
    DRIFT = "DRIFT"
    BUSY = "BUSY"
    TTY_REQUIRED = "TTY_REQUIRED"
    IO_FAILED = "IO_FAILED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True)
class Failure:
    stage: Stage
    reason: Reason
    paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Entry:
    source: Path
    relative: PurePosixPath
    mode: int


@dataclass(frozen=True)
class Plan:
    source: Path
    home: Path
    parent: str
    entries: tuple[Entry, ...]
    local_paths: tuple[Path, ...]


class Phase(Enum):
    PREPARED = "PREPARED"
    ACTIVE = "ACTIVE"
    RESTORED = "RESTORED"


@dataclass(frozen=True)
class Absent:
    pass


@dataclass(frozen=True)
class RegularFile:
    sha256: str
    mode: int


@dataclass(frozen=True)
class SymbolicLink:
    target: str
    mode: int


FileState = Absent | RegularFile | SymbolicLink


@dataclass(frozen=True)
class SnapshotItem:
    relative: PurePosixPath
    before: FileState
    after: FileState


@dataclass(frozen=True)
class Snapshot:
    phase: Phase
    home: Path
    head: str
    items: tuple[SnapshotItem, ...]
    directories: tuple[PurePosixPath, ...]

    def at(self, phase: Phase) -> Snapshot:
        return Snapshot(phase, self.home, self.head, self.items, self.directories)


def state_json(value: FileState) -> dict:
    match value:
        case Absent():
            return {"type": "ABSENT"}
        case RegularFile(sha256, mode):
            return {"type": "FILE", "sha256": sha256, "mode": mode}
        case SymbolicLink(target, mode):
            return {"type": "SYMLINK", "target": target, "mode": mode}


def refined_state(value: dict) -> FileState:
    # Called only after the closed boundary parser proves valid_fingerprint.
    match value["type"]:
        case "ABSENT":
            return Absent()
        case "FILE":
            return RegularFile(value["sha256"], value["mode"])
        case "SYMLINK":
            return SymbolicLink(value["target"], value["mode"])


SUPPORT = PurePosixPath(".local/share/shell-config")
CORE = (
    ".zshrc", ".zprofile", "config/mise/config.toml", "config/starship.toml",
    "config/prompt.json", "config/atuin/config.toml", "config/ai/config.json",
    "bin/ai", "bin/shell-prompt", "bin/prompt-editor", "bin/config", "bin/dotfiles",
    "ai/core.mjs", "ai/config.mjs", "ai/main.mjs", "ai/ui.mjs",
    "prompt/context.mjs", "prompt/model.mjs", "prompt/server.mjs",
    "prompt/editor.mjs", "prompt/editor.html", "dotfiles.py", "tests/dotfiles.py",
)
OPTIONAL = (
    "README.md", ".zshrc.local.example", "docs/ai.md", "docs/cutover.md",
    "docs/dotfiles.md", "docs/pi.md", "pi/settings.public.json",
    "pi/packages.public.json", "pi/sync-settings.py", "pi/pi-lsp.json",
    "pi/agents/scout.md", "pi/agents/worker.md", "pi/agents/researcher.md",
    "pi/prompts/verify.md",
    "bin/shell-tools", "bin/keys", "bin/project", "bin/scratch", "bin/work", "bin/review",
    "terminal/main.py", "terminal/keys.py", "terminal/navigation.py", "terminal/work.py", "terminal/configure.py",
    "config/zsh/functions/y", "config/zsh/functions/p", "config/zsh/functions/rr", "config/zsh/functions/f", "config/zsh/completions/_config", "config/zsh/completions/_work",
    "config/nvim/init.lua", "config/nvim/lua/editor/options.lua", "config/nvim/lua/editor/keymaps.lua",
    "config/nvim/lua/editor/lsp.lua", "config/nvim/lua/editor/plugins.lua", "config/nvim/lua/editor/health.lua",
    "config/yazi/yazi.toml", "config/yazi/keymap.toml", "config/work/config.json",
    "pi/shell-context.md", "docs/terminal.md", "docs/editor.md", "docs/work.md", "docs/config-shell.md",
    "tests/helpers.py", "tests/editor.py", "tests/work.py", "tests/configure.py", "tests/work-completion.py",
)


def git(cwd: Path, *args: str, repository: Path | None = None,
        input_data: bytes | None = None, identity: dict | None = None,
        stage: Stage = Stage.REPOSITORY) -> bytes | Failure:
    # Caller Git environment must not redirect this explicit repository boundary.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if identity:
        env.update(identity)
    command = ["git", "-C", str(cwd)]
    if repository is not None:
        command += [f"--git-dir={repository}", f"--work-tree={cwd}"]
    result = subprocess.run(command + list(args), input=input_data, capture_output=True, env=env)
    if result.returncode:
        return Failure(stage, Reason.GIT_FAILED)
    return result.stdout


def mapped(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if name in (".zshrc", ".zprofile"):
        return path
    if path.parts[0] == "config":
        return PurePosixPath(".config", *path.parts[1:])
    if path.parts[0] == "bin":
        return PurePosixPath(".local/bin", *path.parts[1:])
    if name == "pi/pi-lsp.json" or name.startswith(("pi/agents/", "pi/prompts/")):
        return PurePosixPath(".pi/agent", *path.parts[1:])
    return SUPPORT / path


def safe_leaf(home: Path, target: Path) -> Failure | None:
    if not target.is_relative_to(home) or target == home:
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (target,))
    for ancestor in target.parents:
        if ancestor == home:
            break
        if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
            return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (ancestor,))
    if target.exists() and not target.is_file() and not target.is_symlink():
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (target,))
    return None


def roots(home: Path, state: Path) -> Failure | None:
    defaults = {"ZDOTDIR": home, "XDG_CONFIG_HOME": home / ".config",
                "XDG_DATA_HOME": home / ".local/share", "XDG_STATE_HOME": home / ".local/state",
                "PI_CODING_AGENT_DIR": home / ".pi/agent",
                "SHELL_CONFIG_STATE_DIR": home / ".local/state/shell-config-cutover"}
    for key, expected in defaults.items():
        if os.environ.get(key) and Path(os.environ[key]).absolute() != expected:
            return Failure(Stage.PREFLIGHT, Reason.UNSUPPORTED_ROOT, (Path(os.environ[key]),))
    if not home.is_dir() or home.is_symlink() or state.is_symlink() or state == home:
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (state,))
    if state.is_relative_to(home):
        return safe_leaf(home, state / "manifest.json")
    # A separately selected private backup root is an explicit effect boundary.
    if state.exists() and not state.is_dir():
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (state,))
    return None


def make_plan(source: Path, home: Path, state: Path) -> Plan | Failure:
    rejected = roots(home, state)
    if rejected:
        return rejected
    if os.path.lexists(home / ".cfg"):
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_EXISTS, (home / ".cfg",))
    state_authority = state.resolve()
    if state_authority == source or state_authority.is_relative_to(source) or source.is_relative_to(state_authority):
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (state,))
    parent = git(source, "rev-parse", "--verify", "HEAD")
    if isinstance(parent, Failure):
        return parent
    entries = []
    for name in CORE + OPTIONAL:
        file = source / name
        if name in OPTIONAL and not os.path.lexists(file):
            continue
        if not file.is_file() or file.is_symlink():
            return Failure(Stage.PREFLIGHT, Reason.INVALID_LAYOUT, (file,))
        rejected = safe_leaf(source, file)
        if rejected:
            return rejected
        target = mapped(name)
        rejected = safe_leaf(home, home / target)
        if rejected:
            return rejected
        mode = 0o755 if name.startswith("bin/") else 0o644
        if name.startswith("config/") or name in (".zshrc", ".zprofile"):
            mode = 0o600
        entries.append(Entry(file, target, mode))
    # These are local effects, never Git inputs. The old snapshot stays intact.
    local_paths = (home / ".zshpath", home / ".config/mise/mise.lock",
                   home / ".local/state/shell-config-cutover/active")
    for target in (home / ".gitignore", *local_paths):
        rejected = safe_leaf(home, target)
        if rejected:
            return rejected
    for target in (home / ".cfg", home / ".gitignore", *local_paths, *(home / e.relative for e in entries)):
        target_authority = target.parent.resolve() / target.name
        if (state_authority == target_authority or state_authority.is_relative_to(target_authority) or
                target_authority.is_relative_to(state_authority)):
            return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH, (state,))
    if source.is_relative_to(home / SUPPORT):
        return Failure(Stage.PREFLIGHT, Reason.INVALID_LAYOUT, (source,))
    return Plan(source, home, parent.decode().strip(), tuple(entries), local_paths)


def ignore_file(entries: tuple[Entry, ...]) -> bytes:
    # Ignore-by-default makes adding a new private file an explicit `add -f` act.
    paths = {".gitignore"}
    for entry in entries:
        paths.add(entry.relative.as_posix())
        for parent in entry.relative.parents:
            if parent != PurePosixPath("."):
                paths.add(parent.as_posix() + "/")
    return ("# New dotfiles require explicit config add -f -- <path>.\n*\n" +
            "".join(f"!/{path}\n" for path in sorted(paths)) ).encode()


def fingerprint(path: Path) -> FileState:
    if path.is_symlink():
        return SymbolicLink(os.readlink(path), stat.S_IMODE(path.lstat().st_mode))
    if path.is_file():
        return RegularFile(hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    return Absent()


def valid_fingerprint(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    match value.get("type"):
        case "ABSENT":
            return set(value) == {"type"}
        case "SYMLINK":
            return (set(value) == {"type", "target", "mode"} and isinstance(value["target"], str) and
                    type(value["mode"]) is int and 0 <= value["mode"] <= 0o7777)
        case "FILE":
            return (set(value) == {"type", "sha256", "mode"} and
                    isinstance(value["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", value["sha256"])) and
                    type(value["mode"]) is int and 0 <= value["mode"] <= 0o7777)
        case _:
            return False


def write_manifest(state: Path, manifest: Snapshot) -> None:
    temp = state / "manifest.next"
    value = {"type": manifest.phase.value, "home": str(manifest.home), "head": manifest.head,
             "directories": [p.as_posix() for p in manifest.directories],
             "items": [{"path": item.relative.as_posix(), "before": state_json(item.before),
                        "after": state_json(item.after)} for item in manifest.items]}
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.chmod(0o600)
    os.replace(temp, state / "manifest.json")


def load_snapshot(home: Path, state: Path) -> Snapshot | Failure:
    if (state / "manifest.json").is_symlink():
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    try:
        value = json.loads((state / "manifest.json").read_text())
    except (ValueError, OSError):
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    if (not isinstance(value, dict) or set(value) != {"type", "home", "head", "items", "directories"} or
            value["type"] not in ("PREPARED", "ACTIVE", "RESTORED") or value["home"] != str(home) or
            not isinstance(value["head"], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["head"]) or
            not isinstance(value["items"], list) or not value["items"] or not isinstance(value["directories"], list)):
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    seen = set()
    allowed = {mapped(name).as_posix() for name in CORE + OPTIONAL}
    allowed |= {".gitignore", ".zshpath", ".config/mise/mise.lock", ".local/state/shell-config-cutover/active"}
    for item in value["items"]:
        if (not isinstance(item, dict) or set(item) != {"path", "before", "after"} or
                not isinstance(item["path"], str) or item["path"] not in allowed or item["path"] in seen or
                not valid_fingerprint(item["before"]) or not valid_fingerprint(item["after"])):
            return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
        seen.add(item["path"])
        rejected = safe_leaf(home, home / item["path"])
        if rejected:
            return rejected
        rejected = safe_leaf(state, state / "files" / item["path"])
        if rejected or fingerprint(state / "files" / item["path"]) != refined_state(item["before"]):
            return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    required = {mapped(name).as_posix() for name in CORE} | {
        ".gitignore", ".zshpath", ".config/mise/mise.lock", ".local/state/shell-config-cutover/active"}
    if not required.issubset(seen):
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    baseline = state / "repository.git"
    if baseline.is_symlink() or not baseline.is_dir():
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    inventory = git(state, "ls-tree", "-r", "--name-only", "-z", value["head"], repository=baseline, stage=Stage.RESTORE)
    if isinstance(inventory, Failure):
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    tracked = {os.fsdecode(path) for path in inventory.split(b"\0") if path}
    if seen != tracked | {".zshpath", ".config/mise/mise.lock", ".local/state/shell-config-cutover/active"}:
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    directories = {parent.as_posix() for name in seen for parent in PurePosixPath(name).parents if parent != PurePosixPath('.')}
    if (any(not isinstance(path, str) or path not in directories for path in value["directories"]) or
            len(set(value["directories"])) != len(value["directories"])):
        return Failure(Stage.RESTORE, Reason.INVALID_SNAPSHOT)
    return Snapshot(Phase(value["type"]), home, value["head"],
                    tuple(SnapshotItem(PurePosixPath(item["path"]), refined_state(item["before"]),
                                       refined_state(item["after"])) for item in value["items"]),
                    tuple(PurePosixPath(path) for path in value["directories"]))


def show_plan(plan: Plan) -> None:
    print("\nDotfiles / migration preview\n")
    print(f"Git repository: {plan.home / '.cfg'}\nWorktree: {plan.home}")
    for entry in plan.entries:
        print(f"  {entry.source.relative_to(plan.source)} -> ~/{entry.relative}")
    print("  ~/.gitignore -> explicit tracking rules")
    print("  ~/.zshpath -> private plain file (untracked)")
    print("  Existing mise.lock and legacy active marker -> private backup")
    print("\nCurrent public checkout contents become a child commit on branch dotfiles.")
    print("Existing files and links are backed up. Tools are not installed; nothing is pushed.")


def confirm(prompt: str) -> bool | Failure:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return Failure(Stage.MENU, Reason.TTY_REQUIRED)
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def build_repository(plan: Plan, destination: Path, tree: Path) -> str | Failure:
    result = git(plan.source, "clone", "--bare", "--no-hardlinks", "--quiet", str(plan.source), str(destination))
    if isinstance(result, Failure):
        return result
    # clone's origin would otherwise point at the old local checkout.
    origin = subprocess.run(["git", "-C", str(plan.source), "config", "--get", "remote.origin.url"], capture_output=True)
    if origin.returncode not in (0, 1):
        return Failure(Stage.REPOSITORY, Reason.GIT_FAILED)
    args = ("remote", "set-url", "origin", origin.stdout.decode().strip()) if origin.returncode == 0 else ("remote", "remove", "origin")
    result = git(tree, *args, repository=destination)
    if isinstance(result, Failure):
        return result
    identity = {}
    for field, variables in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")),
                             ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        if all(os.environ.get(key) for key in variables):
            identity.update({key: os.environ[key] for key in variables})
        else:
            result = git(plan.source, "config", "--get", field)
            if isinstance(result, Failure):
                return result
            identity.update({key: result.decode().strip() for key in variables})
    # An empty index is essential: old checkout paths must not survive in HOME.
    for args in (("read-tree", "--empty"), ("add", "--force", "--all"), ("write-tree",)):
        result = git(tree, *args, repository=destination)
        if isinstance(result, Failure):
            return result
    commit = git(tree, "commit-tree", result.decode().strip(), "-p", plan.parent,
                 "-m", "Migrate shell-config to a bare HOME worktree", repository=destination, identity=identity)
    if isinstance(commit, Failure):
        return commit
    head = commit.decode().strip()
    for args in (("update-ref", "refs/heads/dotfiles", head),
                 ("symbolic-ref", "HEAD", "refs/heads/dotfiles"),
                 ("config", "status.showUntrackedFiles", "no")):
        result = git(tree, *args, repository=destination)
        if isinstance(result, Failure):
            return result
    return head


def copy_leaf(source: Path, target: Path) -> Failure | None:
    if not source.is_symlink() and not source.is_file():
        target.unlink(missing_ok=True)
        return None
    before = fingerprint(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".dotfiles-", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        if source.is_symlink():
            temporary.unlink()
            temporary.symlink_to(os.readlink(source))
            if hasattr(os, "lchmod"):
                os.lchmod(temporary, stat.S_IMODE(source.lstat().st_mode))
        else:
            shutil.copy2(source, temporary)
        if fingerprint(target) != before:
            return Failure(Stage.CHECKOUT, Reason.DRIFT, (target,))
        os.replace(temporary, target)
        return None
    finally:
        temporary.unlink(missing_ok=True)


def reserve_repository(repository: Path, head: str) -> Failure | None:
    # Defer catchable OS signals only across the bounded ownership publication.
    # Once unblocked, recovery can identify even an incomplete repository copy.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    try:
        try:
            repository.mkdir(mode=0o700)
        except FileExistsError:
            return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_EXISTS, (repository,))
        identity = repository.stat()
        owner = repository / "migration-owner"
        owned_entry = None
        try:
            with owner.open("x") as output:
                owned_entry = owner.stat()
                output.write(head)
        except BaseException:
            current = repository.stat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                contents = set(repository.iterdir())
                if not contents:
                    repository.rmdir()
                elif contents == {owner} and owned_entry and not owner.is_symlink():
                    current_entry = owner.stat()
                    if (current_entry.st_dev, current_entry.st_ino) == (owned_entry.st_dev, owned_entry.st_ino):
                        owner.unlink()
                        repository.rmdir()
            raise
        return None
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def migrate_locked(plan: Plan, state: Path) -> Failure | None:
    if os.path.lexists(state / "manifest.json") or any(p.name != "lock" for p in state.iterdir()):
        return Failure(Stage.SNAPSHOT, Reason.BUSY, (state,))
    if os.path.lexists(plan.home / ".cfg"):
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_EXISTS, (plan.home / ".cfg",))
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=state) as temporary:
        temp = Path(temporary)
        tree = temp / "tree"
        tree.mkdir()
        for entry in plan.entries:
            target = tree / entry.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry.source, target)
            target.chmod(entry.mode)
        (tree / ".gitignore").write_bytes(ignore_file(plan.entries))
        head = build_repository(plan, temp / "repository.git", tree)
        if isinstance(head, Failure):
            return head
        # Retain immutable Git authority for the complete imported inventory,
        # including optional files, even through an interrupted publication.
        baseline = state / "repository.git"
        os.rename(temp / "repository.git", baseline)
        print("dotfiles: stage=repository outcome=COMPLETE", file=sys.stderr)
        saved_path = temp / "path"
        if (plan.home / ".zshpath").exists():
            saved_path.write_bytes((plan.home / ".zshpath").read_bytes())
        else:
            saved_path.write_text(os.environ.get("PATH", "") + "\n")
        saved_path.chmod(0o600)
        replacements = {entry.relative.as_posix(): tree / entry.relative for entry in plan.entries}
        replacements[".gitignore"] = tree / ".gitignore"
        replacements[".zshpath"] = saved_path
        replacements[".config/mise/mise.lock"] = None
        replacements[".local/state/shell-config-cutover/active"] = None
        items = []
        for name, replacement in replacements.items():
            target = plan.home / name
            rejected = safe_leaf(plan.home, target)
            if rejected:
                return rejected
            before = fingerprint(target)
            backup = state / "files" / name
            rejected = safe_leaf(state, backup)
            if rejected:
                return rejected
            result = copy_leaf(target, backup)
            if result:
                return result
            if fingerprint(backup) != before:
                return Failure(Stage.SNAPSHOT, Reason.DRIFT, (target,))
            items.append(SnapshotItem(PurePosixPath(name), before,
                                      fingerprint(replacement) if replacement else Absent()))
        directories = {parent.relative_to(plan.home).as_posix()
                       for name in replacements for parent in (plan.home / name).parents
                       if parent.is_relative_to(plan.home) and parent != plan.home and not parent.exists()}
        manifest = Snapshot(Phase.PREPARED, plan.home, head, tuple(items),
                            tuple(PurePosixPath(path) for path in sorted(directories)))
        # Revalidate the entire preimage before publishing recovery authority.
        for item in items:
            target = plan.home / item.relative
            if safe_leaf(plan.home, target) or fingerprint(target) != item.before:
                return Failure(Stage.SNAPSHOT, Reason.DRIFT, (target,))
        # Reserve the repository destination exclusively before any HOME write.
        # Directory rename alone can silently replace an unrelated empty .cfg.
        repository = plan.home / ".cfg"
        write_manifest(state, manifest)  # Durable recovery authority before the first HOME write.
        result = reserve_repository(repository, head)
        if result:
            return result
        shutil.copytree(baseline, repository, dirs_exist_ok=True)
        print("dotfiles: stage=snapshot outcome=COMPLETE", file=sys.stderr)
        for name, replacement in replacements.items():
            target = plan.home / name
            item = next(item for item in items if item.relative.as_posix() == name)
            if safe_leaf(plan.home, target) or fingerprint(target) != item.before:
                return Failure(Stage.CHECKOUT, Reason.DRIFT, (target,))
            if replacement:
                result = copy_leaf(replacement, target)
                if result:
                    return result
            else:
                target.unlink(missing_ok=True)
            if fingerprint(target) != item.after:
                return Failure(Stage.CHECKOUT, Reason.DRIFT, (target,))
        result = git(plan.home, "diff", "--quiet", "HEAD", repository=plan.home / ".cfg", stage=Stage.CHECKOUT)
        if isinstance(result, Failure):
            return result
        manifest = manifest.at(Phase.ACTIVE)
        write_manifest(state, manifest)
        (repository / "migration-owner").unlink()
        print("dotfiles: stage=checkout outcome=COMPLETE", file=sys.stderr)
    print("Migration complete. HOME is now the configuration authority.")
    print("Open a fresh terminal, then use config status or dotfiles.")
    print(f"Rollback: dotfiles restore --yes (backup: {state})")
    return None


def restore_locked(home: Path, state: Path) -> Failure | None:
    snapshot = load_snapshot(home, state)
    if isinstance(snapshot, Failure):
        return snapshot
    if snapshot.phase is Phase.RESTORED:
        print("Already restored; the migrated Git repository is retained in the backup.")
        return None
    repo = home / ".cfg"
    if os.path.lexists(state / "restored.git") and os.path.lexists(repo):
        return Failure(Stage.RESTORE, Reason.BUSY, (state / "restored.git",))
    if os.path.lexists(repo):
        if repo.is_symlink() or not repo.is_dir():
            return Failure(Stage.RESTORE, Reason.UNSAFE_PATH, (repo,))
        owner = repo / "migration-owner"
        prepared_owned = (snapshot.phase is Phase.PREPARED and owner.is_file() and
                          not owner.is_symlink() and owner.read_text() == snapshot.head)
        if not prepared_owned:
            head = git(home, "rev-parse", "HEAD", repository=repo, stage=Stage.RESTORE)
            if isinstance(head, Failure):
                return head
            if head.decode().strip() != snapshot.head:
                return Failure(Stage.RESTORE, Reason.DRIFT, (repo,))
            index = git(home, "diff", "--cached", "--quiet", "HEAD", repository=repo, stage=Stage.RESTORE)
            if isinstance(index, Failure):
                return Failure(Stage.RESTORE, Reason.DRIFT, (repo,))
    elif snapshot.phase is Phase.ACTIVE:
        return Failure(Stage.RESTORE, Reason.DRIFT, (repo,))
    drift = []
    for item in snapshot.items:
        current = fingerprint(home / item.relative)
        permitted = (item.after, item.before) if snapshot.phase is Phase.PREPARED else (item.after,)
        if current not in permitted:
            drift.append(home / item.relative)
    if drift:
        return Failure(Stage.RESTORE, Reason.DRIFT, tuple(drift))
    # PREPARED accepts either side of each write, making interruption resumable.
    snapshot = snapshot.at(Phase.PREPARED)
    write_manifest(state, snapshot)
    if repo.exists():
        os.rename(repo, state / "restored.git")
    for item in snapshot.items:
        if safe_leaf(home, home / item.relative):
            return Failure(Stage.RESTORE, Reason.UNSAFE_PATH, (home / item.relative,))
        # An edit made after whole-surface validation is still not ours to erase.
        if fingerprint(home / item.relative) not in (item.before, item.after):
            return Failure(Stage.RESTORE, Reason.DRIFT, (home / item.relative,))
        result = copy_leaf(state / "files" / item.relative, home / item.relative)
        if result:
            return result
        if fingerprint(home / item.relative) != item.before:
            return Failure(Stage.RESTORE, Reason.DRIFT, (home / item.relative,))
    for name in sorted(snapshot.directories, key=lambda p: len(p.parts), reverse=True):
        directory = home / name
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    snapshot = snapshot.at(Phase.RESTORED)
    write_manifest(state, snapshot)
    print("dotfiles: stage=restore outcome=COMPLETE", file=sys.stderr)
    print("Restored the exact pre-migration files, links, modes, and absence.")
    print("Open a fresh terminal. The migrated Git history remains in the private backup.")
    return None


def locked(state: Path, operation, *args) -> Failure | None:
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.chmod(0o700)
    lock = state / "lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        return Failure(Stage.SNAPSHOT, Reason.BUSY, (lock,))
    try:
        return operation(*args, state)
    finally:
        lock.rmdir()


def migrate(plan: Plan, state: Path) -> Failure | None:
    return locked(state, migrate_locked, plan)


def restore(home: Path, state: Path) -> Failure | None:
    return locked(state, restore_locked, home)


def active_repository(home: Path) -> Failure | None:
    repo = home / ".cfg"
    if repo.is_symlink() or not repo.is_dir():
        return Failure(Stage.PREFLIGHT, Reason.INVALID_LAYOUT, (repo,))
    # --work-tree makes Git report false even for a bare on-disk repository.
    bare = git(repo, "config", "--bool", "core.bare")
    if isinstance(bare, Failure):
        return bare
    if bare.strip() != b"true":
        return Failure(Stage.PREFLIGHT, Reason.INVALID_LAYOUT, (repo,))
    return None


def menu(home: Path, source: Path, state: Path) -> Failure | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return Failure(Stage.MENU, Reason.TTY_REQUIRED)
    if not os.path.lexists(home / ".cfg"):
        plan = make_plan(source, home, state)
        if isinstance(plan, Failure):
            return plan
        show_plan(plan)
        decision = confirm("Migrate these files now?")
        if isinstance(decision, Failure):
            return decision
        if not decision:
            print("Cancelled; no files changed.")
            return None
        result = migrate(plan, state)
        if result:
            return result
    rejected = active_repository(home)
    if rejected:
        return rejected
    while True:
        print("\nDotfiles / HOME\n\n1  Status\n2  Diff\n3  Choose tracked files to stage\n4  Commit staged files\n5  Restore migration backup\nq  Quit\n")
        try:
            choice = input("Choose: ").strip().lower()
        except EOFError:
            return None
        match choice:
            case "q" | "":
                return None
            case "1" | "2":
                result = git(home, *( ("status", "--short") if choice == "1" else ("diff", "HEAD", "--") ), repository=home / ".cfg", stage=Stage.MENU)
                if isinstance(result, Failure):
                    return result
                print(result.decode(errors="replace") or "No changes.")
            case "3":
                result = git(home, "ls-files", "--modified", "--deleted", "-z", repository=home / ".cfg", stage=Stage.MENU)
                if isinstance(result, Failure):
                    return result
                paths = list(dict.fromkeys(os.fsdecode(p) for p in result.split(b"\0") if p))
                if not paths:
                    print("No unstaged tracked changes.")
                    continue
                for index, path in enumerate(paths, 1):
                    print(f"{index}  {path!r}")
                selected = input("Numbers to stage (space-separated; Enter cancels): ").split()
                if not selected:
                    continue
                if any(len(n) > 9 or not n.isdecimal() or not 1 <= int(n) <= len(paths) for n in selected):
                    print("Choose only listed numbers; no files staged.")
                    continue
                result = git(home, "--literal-pathspecs", "add", "--", *(paths[int(n)-1] for n in selected), repository=home / ".cfg", stage=Stage.MENU)
                if isinstance(result, Failure):
                    return result
                print("Selected files staged. Review with config diff --cached.")
            case "4":
                message = input("Commit message (Enter cancels): ").strip()
                if message:
                    result = git(home, "commit", "-m", message, repository=home / ".cfg", stage=Stage.MENU)
                    if isinstance(result, Failure):
                        return result
                    print("Committed locally. Push explicitly with config push.")
            case "5":
                decision = confirm("Restore the pre-migration files?")
                if isinstance(decision, Failure):
                    return decision
                if decision:
                    return restore(home, state)
            case _:
                print("Choose 1-5 or q.")


def main() -> Failure | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="menu", choices=("menu", "plan", "migrate", "restore", "status"))
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--yes", action="store_true", help="apply migrate/restore without the terminal confirmation")
    args = parser.parse_args()
    if not os.environ.get("HOME") or not shutil.which("git"):
        return Failure(Stage.PREFLIGHT, Reason.INVALID_LAYOUT)
    home = Path(os.environ["HOME"]).absolute()
    state = Path(os.environ.get("DOTFILES_STATE_DIR", str(home / ".local/state/shell-config-dotfiles"))).absolute()
    rejected = roots(home, state)
    if rejected:
        return rejected
    match args.command:
        case "menu":
            return menu(home, args.source.resolve(), state)
        case "status":
            rejected = active_repository(home)
            if rejected:
                return rejected
            result = git(home, "status", "--short", repository=home / ".cfg")
            if isinstance(result, Failure):
                return result
            print(result.decode(errors="replace") or "No changes.")
            return None
        case "restore":
            if not args.yes:
                decision = confirm("Restore the pre-migration files?")
                if isinstance(decision, Failure):
                    return decision
                if not decision:
                    return None
            return restore(home, state)
        case "plan" | "migrate":
            if os.path.lexists(home / ".cfg"):
                snapshot = load_snapshot(home, state)
                if not isinstance(snapshot, Failure) and snapshot.phase is Phase.ACTIVE:
                    rejected = active_repository(home)
                    if rejected:
                        return rejected
                    print("Already migrated. Edit HOME files and use config to track changes.")
                    return None
            plan = make_plan(args.source.resolve(), home, state)
            if isinstance(plan, Failure):
                return plan
            show_plan(plan)
            if args.command == "plan":
                return None
            if not args.yes:
                decision = confirm("Migrate these files now?")
                if isinstance(decision, Failure):
                    return decision
                if not decision:
                    return None
            return migrate(plan, state)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        failure = main()
    except (KeyboardInterrupt, EOFError):
        failure = Failure(Stage.MENU, Reason.INTERRUPTED)
    except (OSError, UnicodeError):
        failure = Failure(Stage.CHECKOUT, Reason.IO_FAILED)
    if failure:
        print(f"dotfiles: stage={failure.stage.value} outcome={failure.reason.value}", file=sys.stderr)
        for path in failure.paths[:20]:
            print(f"  {str(path)!r}", file=sys.stderr)
        print("If migration started, its private snapshot is retained; use dotfiles restore --yes to recover.", file=sys.stderr)
        sys.exit(2)

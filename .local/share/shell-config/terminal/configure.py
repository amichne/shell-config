#!/usr/bin/env python3
"""Prepare Pi candidates, then install and commit one verified local changeset."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid


class Stage(Enum):
    PREFLIGHT = "preflight"
    SESSION = "session"
    AGENT = "agent"
    PROPOSAL = "proposal"
    VALIDATE = "validate"
    INSTALL = "install"
    COMMIT = "commit"
    ROLLBACK = "rollback"


class Reason(Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNSUPPORTED_ROOT = "UNSUPPORTED_ROOT"
    UNSAFE_PATH = "UNSAFE_PATH"
    PRIVATE_PATH = "PRIVATE_PATH"
    TOOL_COLLISION = "TOOL_COLLISION"
    MISSING_TOOL = "MISSING_TOOL"
    REPOSITORY_INVALID = "REPOSITORY_INVALID"
    GIT_FAILED = "GIT_FAILED"
    BUSY = "BUSY"
    DRIFT = "DRIFT"
    PREEXISTING_EDIT = "PREEXISTING_EDIT"
    INVALID_SESSION = "INVALID_SESSION"
    AGENT_FAILED = "AGENT_FAILED"
    NO_FINISH_INTENT = "NO_FINISH_INTENT"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    SYNTAX_FAILED = "SYNTAX_FAILED"
    LINT_FAILED = "LINT_FAILED"
    BEHAVIOR_FAILED = "BEHAVIOR_FAILED"
    NO_CHANGES = "NO_CHANGES"
    COMMIT_FAILED = "COMMIT_FAILED"
    IO_FAILED = "IO_FAILED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class Failure:
    stage: Stage
    reason: Reason
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Passed:
    pass


PASSED = Passed()


@dataclass(frozen=True)
class PublicPath:
    relative: PurePosixPath

    def __str__(self) -> str:
        return self.relative.as_posix()


class Kind(Enum):
    COMMAND = "COMMAND"
    FUNCTION = "FUNCTION"
    CONFIG = "CONFIG"
    DOCUMENT = "DOCUMENT"


class HelperKind(Enum):
    ANY = "ANY"
    COMMAND = "COMMAND"
    FUNCTION = "FUNCTION"


class SessionPhase(Enum):
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PendingReceipt:
    phase: SessionPhase
    sha256: str


@dataclass(frozen=True)
class CommittedReceipt:
    sha256: str
    commit: str


Receipt = PendingReceipt | CommittedReceipt


@dataclass(frozen=True)
class Unpublished:
    pass


@dataclass(frozen=True)
class RefAttempted:
    commit: str


@dataclass(frozen=True)
class IndexAttempted:
    commit: str


Publication = Unpublished | RefAttempted | IndexAttempted


@dataclass(frozen=True)
class Absent:
    pass


@dataclass(frozen=True)
class RegularFile:
    sha256: str
    mode: int


FileState = Absent | RegularFile


@dataclass(frozen=True)
class Snapshot:
    path: PublicPath
    before: FileState


@dataclass(frozen=True)
class Add:
    name: str
    kind: HelperKind


@dataclass(frozen=True)
class Edit:
    path: PublicPath


@dataclass(frozen=True)
class Change:
    paths: tuple[PublicPath, ...]


Intent = Add | Edit | Change


@dataclass(frozen=True)
class Repository:
    home: Path
    directory: Path
    head: str
    ref: str
    index_sha256: str
    index_mode: int


@dataclass(frozen=True)
class Session:
    identifier: str
    directory: Path
    repository: Repository
    intent: Intent
    snapshots: tuple[Snapshot, ...]

    @property
    def workspace(self) -> Path:
        return self.directory / "workspace"


@dataclass(frozen=True)
class ExpectExit:
    code: int


@dataclass(frozen=True)
class ExpectStdout:
    code: int
    contains: str


@dataclass(frozen=True)
class Smoke:
    kind: Kind
    path: PublicPath
    args: tuple[str, ...]
    expect: ExpectExit | ExpectStdout


@dataclass(frozen=True)
class Proposal:
    summary: str
    paths: tuple[PublicPath, ...]
    checks: tuple[Smoke, ...]


@dataclass(frozen=True)
class Candidate:
    path: PublicPath
    data: bytes
    mode: int
    before: FileState


@dataclass(frozen=True)
class ProcessResult:
    code: int
    stdout: bytes


NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
MAX_FILES = 128
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_PROCESS_BYTES = 64 * 1024
RESERVED = frozenset({"ai", "config", "dotfiles", "shell-prompt", "prompt-editor", "rr", "f",
                      "zs", "ls", "l", "main", "wt"})
CONFIG_ROOTS = frozenset({"mise", "atuin", "nvim", "yazi", "work", "worktrunk", "ghostty"})
PRIVATE_COMPONENT = re.compile(r"(?:\A|[._-])(?:auth|credentials?|secrets?|tokens?|history|cache|sessions?|trust|state|private|local)(?:[._-]|\Z)", re.I)
FORMATS = frozenset({".toml", ".json", ".lua", ".py", ".sh", ".bash", ".zsh", ".vim", ".conf", ".ini"})
REFERENCES = (".zshrc", ".zprofile", ".config/mise/config.toml", ".config/starship.toml",
              ".config/atuin/config.toml", ".config/ai/config.json")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def event(stage: Stage, outcome: str, *, session: str = "", count: int = 0, commit: str = "") -> None:
    # Outcomes and counts are the entire diagnostic payload; never source or command output.
    fields = [f"stage={stage.value}", f"outcome={outcome}"]
    if session:
        fields.append(f"session={session}")
    if count:
        fields.append(f"files={count}")
    if commit:
        fields.append(f"commit={commit}")
    print("config-shell: " + " ".join(fields), flush=True)


def clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(home: Path, *args: str, index: Path | None = None,
        input_data: bytes | None = None, stage: Stage = Stage.PREFLIGHT) -> ProcessResult | Failure:
    env = clean_env()
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(home),
                                 f"--git-dir={home / '.cfg'}", f"--work-tree={home}", *args],
                                env=env, input=input_data, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return Failure(stage, Reason.GIT_FAILED)
    if result.returncode or len(result.stdout) + len(result.stderr) > MAX_TOTAL_BYTES:
        return Failure(stage, Reason.COMMIT_FAILED if stage == Stage.COMMIT else Reason.GIT_FAILED)
    return ProcessResult(result.returncode, result.stdout)


def safe_leaf(root: Path, path: Path, stage: Stage = Stage.PREFLIGHT) -> Passed | Failure:
    if not path.is_relative_to(root) or path == root or root.is_symlink():
        return Failure(stage, Reason.UNSAFE_PATH)
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            return Failure(stage, Reason.UNSAFE_PATH)
        if component != path and component.exists() and not component.is_dir():
            return Failure(stage, Reason.UNSAFE_PATH)
    if path.exists() and not path.is_file():
        return Failure(stage, Reason.UNSAFE_PATH)
    return PASSED


def public_path(raw: object, stage: Stage = Stage.PREFLIGHT) -> PublicPath | Failure:
    if not isinstance(raw, str) or not raw or len(raw) > 512 or any(ord(c) < 32 for c in raw):
        return Failure(stage, Reason.OUT_OF_SCOPE)
    relative = PurePosixPath(raw)
    if relative.is_absolute() or str(relative) != raw or ".." in relative.parts or "\\" in raw:
        return Failure(stage, Reason.OUT_OF_SCOPE)
    if any(part != ".local" and PRIVATE_COMPONENT.search(part) or part.startswith(".") and part not in {
            ".config", ".local", ".zshrc", ".zprofile"} for part in relative.parts):
        return Failure(stage, Reason.PRIVATE_PATH)
    parts = relative.parts
    if raw in {".zshrc", ".zprofile", ".config/starship.toml", ".config/prompt.json", ".config/ai/config.json"}:
        return PublicPath(relative)
    if len(parts) == 3 and parts[:2] == (".local", "bin") and NAME.fullmatch(parts[2]) and parts[2] not in RESERVED:
        return PublicPath(relative)
    if len(parts) == 4 and parts[:3] == (".config", "zsh", "functions") and NAME.fullmatch(parts[3]):
        return PublicPath(relative)
    if len(parts) >= 3 and parts[0] == ".config" and parts[1] in CONFIG_ROOTS and relative.suffix in FORMATS:
        return PublicPath(relative)
    if len(parts) >= 5 and parts[:4] == (".local", "share", "shell-config", "docs") and relative.suffix == ".md":
        return PublicPath(relative)
    return Failure(stage, Reason.OUT_OF_SCOPE)


def kind_of(path: PublicPath) -> Kind:
    raw = str(path)
    if raw.startswith(".local/bin/"):
        return Kind.COMMAND
    if raw.startswith(".config/zsh/functions/"):
        return Kind.FUNCTION
    if raw.startswith(".local/share/shell-config/docs/"):
        return Kind.DOCUMENT
    return Kind.CONFIG


def fingerprint(path: Path, stage: Stage) -> FileState | Failure:
    if not os.path.lexists(path):
        return Absent()
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISREG(info.st_mode) or mode not in {0o600, 0o644, 0o700, 0o755} or info.st_nlink != 1:
        return Failure(stage, Reason.UNSAFE_PATH)
    if info.st_size > MAX_FILE_BYTES:
        return Failure(stage, Reason.INVALID_CANDIDATE)
    return RegularFile(digest(path.read_bytes()), mode)


def roots(home: Path) -> Passed | Failure:
    defaults = {"ZDOTDIR": home, "XDG_CONFIG_HOME": home / ".config",
                "XDG_DATA_HOME": home / ".local/share", "XDG_STATE_HOME": home / ".local/state",
                "PI_CODING_AGENT_DIR": home / ".pi/agent"}
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        return Failure(Stage.PREFLIGHT, Reason.UNSAFE_PATH)
    for key, default in defaults.items():
        if os.environ.get(key) and Path(os.environ[key]).absolute() != default:
            return Failure(Stage.PREFLIGHT, Reason.UNSUPPORTED_ROOT)
    return PASSED


def active_repository(home: Path) -> Repository | Failure:
    rejected = roots(home)
    if isinstance(rejected, Failure):
        return rejected
    directory = home / ".cfg"
    if directory.is_symlink() or not directory.is_dir():
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_INVALID)
    for name in ("HEAD", "config", "index"):
        rejected = safe_leaf(home, directory / name)
        if isinstance(rejected, Failure):
            return rejected
    head = git(home, "rev-parse", "--verify", "HEAD")
    ref = git(home, "symbolic-ref", "--quiet", "HEAD")
    bare = git(home, "config", "--get", "core.bare")
    if any(isinstance(result, Failure) for result in (head, ref, bare)):
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_INVALID)
    head_value, ref_value = head.stdout.decode().strip(), ref.stdout.decode().strip()
    if not OID.fullmatch(head_value) or not ref_value.startswith("refs/heads/") or bare.stdout != b"true\n":
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_INVALID)
    index = directory / "index"
    if not index.is_file() or index.stat().st_nlink != 1 or stat.S_IMODE(index.stat().st_mode) not in {0o600, 0o644}:
        return Failure(Stage.PREFLIGHT, Reason.REPOSITORY_INVALID)
    for relative in (ref_value, "objects/probe", "logs/" + ref_value, "logs/HEAD"):
        rejected = safe_leaf(home, directory / relative)
        if isinstance(rejected, Failure):
            return rejected
    return Repository(home, directory, head_value, ref_value, digest(index.read_bytes()), stat.S_IMODE(index.stat().st_mode))


def tracked_paths(home: Path) -> tuple[PublicPath, ...] | Failure:
    result = git(home, "ls-files", "-z")
    if isinstance(result, Failure):
        return result
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = public_path(os.fsdecode(raw))
        if isinstance(path, PublicPath):
            paths.append(path)
    if len(paths) > MAX_FILES:
        return Failure(Stage.PREFLIGHT, Reason.OUT_OF_SCOPE)
    return tuple(paths)


def collision(home: Path, name: str) -> Passed | Failure:
    if not NAME.fullmatch(name):
        return Failure(Stage.PREFLIGHT, Reason.INVALID_ARGUMENT)
    if name in RESERVED or shutil.which(name):
        return Failure(Stage.PREFLIGHT, Reason.TOOL_COLLISION)
    for relative in (f".local/bin/{name}", f".config/zsh/functions/{name}"):
        rejected = safe_leaf(home, home / relative)
        if isinstance(rejected, Failure):
            return rejected
        if os.path.lexists(home / relative):
            return Failure(Stage.PREFLIGHT, Reason.TOOL_COLLISION)
    targets = (f".local/bin/{name}", f".config/zsh/functions/{name}")
    for arguments in (("ls-files", "--stage", "-z", "--", *targets),
                      ("ls-tree", "-z", "--name-only", "HEAD", "--", *targets)):
        result = git(home, *arguments)
        if isinstance(result, Failure):
            return result
        if result.stdout:
            return Failure(Stage.PREFLIGHT, Reason.PREEXISTING_EDIT)
    if not shutil.which("zsh"):
        return Failure(Stage.PREFLIGHT, Reason.MISSING_TOOL)
    result = subprocess.run(["zsh", "-fc", 'whence -w -- "$1" >/dev/null', "config-shell", name],
                            env=clean_env(), capture_output=True, timeout=5)
    if result.returncode == 0:
        return Failure(Stage.PREFLIGHT, Reason.TOOL_COLLISION)
    return PASSED


def selected_intent(home: Path, args: argparse.Namespace) -> Intent | Failure:
    if args.action == "add":
        if args.name:
            rejected = collision(home, args.name)
            if isinstance(rejected, Failure):
                return rejected
        return Add(args.name or "", HelperKind((args.kind or "any").upper()))
    if args.action == "edit":
        if not NAME.fullmatch(args.name):
            return Failure(Stage.PREFLIGHT, Reason.INVALID_ARGUMENT)
        choices = []
        for relative in (f".local/bin/{args.name}", f".config/zsh/functions/{args.name}"):
            parsed = public_path(relative)
            if isinstance(parsed, PublicPath) and os.path.lexists(home / relative):
                choices.append(parsed)
        if len(choices) != 1:
            return Failure(Stage.PREFLIGHT, Reason.INVALID_ARGUMENT)
        return Edit(choices[0])
    if args.paths:
        paths = []
        for raw in args.paths:
            parsed = public_path(raw)
            if isinstance(parsed, Failure):
                return parsed
            paths.append(parsed)
    else:
        tracked = tracked_paths(home)
        if isinstance(tracked, Failure):
            return tracked
        # This is an explicit public Git inventory, never discovery of HOME.
        paths = [path for path in tracked if kind_of(path) in {Kind.CONFIG, Kind.DOCUMENT}]
    if not paths or len(paths) > MAX_FILES or len(set(paths)) != len(paths):
        return Failure(Stage.PREFLIGHT, Reason.INVALID_ARGUMENT)
    return Change(tuple(paths))


def state_json(state: FileState) -> dict:
    match state:
        case Absent():
            return {"type": "ABSENT"}
        case RegularFile(sha256, mode):
            return {"type": "FILE", "sha256": sha256, "mode": mode}


def intent_json(intent: Intent) -> dict:
    match intent:
        case Add(name, kind):
            return {"type": "ADD", "name": name, "kind": kind.value}
        case Edit(path):
            return {"type": "EDIT", "path": str(path)}
        case Change(paths):
            return {"type": "CHANGE", "paths": [str(path) for path in paths]}


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_write(target: Path, data: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".config-shell-", dir=target.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def private_directory(home: Path, path: Path) -> Passed | Failure:
    rejected = safe_leaf(home, path / "probe", Stage.SESSION)
    if isinstance(rejected, Failure):
        return rejected
    if path.exists():
        info = path.stat()
        if not path.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            return Failure(Stage.SESSION, Reason.UNSAFE_PATH)
    else:
        path.mkdir(parents=True, mode=0o700)
    return PASSED


def session_root(home: Path) -> Path:
    return home / ".local/state/shell-config/sessions"


def receipt_path(session: Session) -> Path:
    return session.repository.directory / "config-shell-authority" / f"{session.identifier}.json"


def write_receipt(session: Session, receipt: Receipt) -> None:
    match receipt:
        case PendingReceipt(phase, sha256):
            value = {"type": phase.value, "sha256": sha256}
        case CommittedReceipt(sha256, commit):
            value = {"type": "COMMITTED", "sha256": sha256, "commit": commit}
    atomic_write(receipt_path(session), encoded(value), 0o600)


def prepare(repository: Repository, intent: Intent, request: str) -> Session | Failure:
    selected = (intent.path,) if isinstance(intent, Edit) else intent.paths if isinstance(intent, Change) else ()
    snapshots = []
    total = 0
    for path in selected:
        target = repository.home / str(path)
        rejected = safe_leaf(repository.home, target)
        if isinstance(rejected, Failure):
            return rejected
        before = fingerprint(target, Stage.PREFLIGHT)
        if isinstance(before, Failure):
            return before
        result = git(repository.home, "diff", "--no-ext-diff", "--quiet", repository.head, "--", str(path))
        indexed = git(repository.home, "ls-files", "--error-unmatch", "--", str(path))
        if isinstance(before, RegularFile) and (isinstance(result, Failure) or isinstance(indexed, Failure)):
            return Failure(Stage.PREFLIGHT, Reason.PREEXISTING_EDIT, (str(path),))
        if isinstance(before, Absent) and not isinstance(indexed, Failure):
            return Failure(Stage.PREFLIGHT, Reason.PREEXISTING_EDIT, (str(path),))
        staged = git(repository.home, "diff", "--cached", "--quiet", repository.head, "--", str(path))
        if isinstance(staged, Failure):
            return Failure(Stage.PREFLIGHT, Reason.PREEXISTING_EDIT, (str(path),))
        total += target.stat().st_size if target.exists() else 0
        snapshots.append(Snapshot(path, before))
    if total > MAX_TOTAL_BYTES:
        return Failure(Stage.PREFLIGHT, Reason.OUT_OF_SCOPE)
    for directory in (session_root(repository.home), repository.directory / "config-shell-authority"):
        rejected = private_directory(repository.home, directory)
        if isinstance(rejected, Failure):
            return rejected
    identifier = uuid.uuid4().hex
    directory = session_root(repository.home) / identifier
    directory.mkdir(mode=0o700)
    session = Session(identifier, directory, repository, intent, tuple(snapshots))
    session.workspace.mkdir(mode=0o700)
    (directory / "agent.lock").touch(mode=0o600)
    (session.workspace / "candidates").mkdir(mode=0o700)
    (directory / "preimages").mkdir(mode=0o700)
    for item in snapshots:
        if isinstance(item.before, RegularFile):
            source = repository.home / str(item.path)
            original = directory / "preimages" / str(item.path)
            candidate = session.workspace / "candidates" / str(item.path)
            original.parent.mkdir(parents=True, exist_ok=True)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(source.read_bytes())
            original.chmod(0o400)
            candidate.write_bytes(source.read_bytes())
            candidate.chmod(item.before.mode)
    authority = {"type": "SESSION", "id": identifier, "home": str(repository.home),
                 "head": repository.head, "ref": repository.ref, "index_sha256": repository.index_sha256,
                 "index_mode": repository.index_mode, "intent": intent_json(intent),
                 "files": [{"path": str(item.path), "before": state_json(item.before)} for item in snapshots]}
    authority_bytes = encoded(authority)
    atomic_write(directory / "authority.json", authority_bytes, 0o400)
    write_receipt(session, PendingReceipt(SessionPhase.RUNNING, digest(authority_bytes)))
    public = {"type": "REQUEST", "session": identifier, "request": request, "intent": intent_json(intent)}
    atomic_write(session.workspace / "request.json", encoded(public), 0o400)
    references = session.workspace / "references"
    references.mkdir(mode=0o700)
    tracked = tracked_paths(repository.home)
    if isinstance(tracked, Failure):
        return tracked
    reference_paths = [path for path in tracked if str(path) in REFERENCES or kind_of(path) == Kind.FUNCTION]
    reference_bytes = 0
    for path in reference_paths:
        source = repository.home / str(path)
        rejected = safe_leaf(repository.home, source, Stage.SESSION)
        if isinstance(rejected, Failure):
            return rejected
        state = fingerprint(source, Stage.SESSION)
        if isinstance(state, Failure):
            return state
        if isinstance(state, RegularFile):
            reference_bytes += source.stat().st_size
            if reference_bytes > MAX_TOTAL_BYTES:
                return Failure(Stage.SESSION, Reason.OUT_OF_SCOPE)
            destination = references / str(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o400)
    context = Path(__file__).resolve().parents[1] / "pi/shell-context.md"
    instructions = context.read_bytes() + b"\n\nCurrent bounded request:\n" + encoded(public)
    atomic_write(session.workspace / "instructions.md", instructions, 0o400)
    event(Stage.SESSION, "PREPARED", session=identifier, count=len(snapshots))
    return session


def exact(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def parse_state(value: object) -> FileState | Failure:
    if exact(value, {"type"}) and value["type"] == "ABSENT":
        return Absent()
    if exact(value, {"type", "sha256", "mode"}) and value["type"] == "FILE" and isinstance(value["sha256"], str) and SHA256.fullmatch(value["sha256"]) and type(value["mode"]) is int and value["mode"] in {0o600, 0o644, 0o700, 0o755}:
        return RegularFile(value["sha256"], value["mode"])
    return Failure(Stage.SESSION, Reason.INVALID_SESSION)


def read_json(path: Path, stage: Stage, reason: Reason) -> object | Failure:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        return Failure(stage, reason)
    def closed_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=closed_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (ValueError, UnicodeError):
        return Failure(stage, reason)


def load_session(home: Path, identifier: str) -> tuple[Session, Receipt] | Failure:
    if not IDENTIFIER.fullmatch(identifier):
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    directory = session_root(home) / identifier
    authority_path = directory / "authority.json"
    receipt = home / ".cfg/config-shell-authority" / f"{identifier}.json"
    for path in (authority_path, receipt):
        rejected = safe_leaf(home, path, Stage.SESSION)
        if isinstance(rejected, Failure):
            return rejected
    record = read_json(receipt, Stage.SESSION, Reason.INVALID_SESSION)
    if isinstance(record, Failure) or not isinstance(record, dict) or not isinstance(record.get("type"), str) or record["type"] not in {"RUNNING", "READY", "FAILED", "COMMITTED"}:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    keys = {"type", "sha256", "commit"} if record["type"] == "COMMITTED" else {"type", "sha256"}
    if not exact(record, keys) or not isinstance(record["sha256"], str) or not SHA256.fullmatch(record["sha256"]):
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    if record["type"] == "COMMITTED":
        if not isinstance(record["commit"], str) or not OID.fullmatch(record["commit"]):
            return Failure(Stage.SESSION, Reason.INVALID_SESSION)
        refined_receipt = CommittedReceipt(record["sha256"], record["commit"])
    else:
        refined_receipt = PendingReceipt(SessionPhase(record["type"]), record["sha256"])
    value = read_json(authority_path, Stage.SESSION, Reason.INVALID_SESSION)
    if isinstance(value, Failure) or not exact(value, {"type", "id", "home", "head", "ref", "index_sha256", "index_mode", "intent", "files"}):
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    if digest(authority_path.read_bytes()) != record["sha256"] or value["type"] != "SESSION" or value["id"] != identifier or value["home"] != str(home):
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    if not all(isinstance(value[key], str) for key in ("head", "ref", "index_sha256")) or not OID.fullmatch(value["head"]) or not SHA256.fullmatch(value["index_sha256"]) or not value["ref"].startswith("refs/heads/") or type(value["index_mode"]) is not int or value["index_mode"] not in {0o600, 0o644}:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    raw_intent = value["intent"]
    if exact(raw_intent, {"type", "name", "kind"}) and raw_intent["type"] == "ADD" and isinstance(raw_intent["name"], str) and (not raw_intent["name"] or NAME.fullmatch(raw_intent["name"])) and isinstance(raw_intent["kind"], str) and raw_intent["kind"] in {"ANY", "COMMAND", "FUNCTION"}:
        intent = Add(raw_intent["name"], HelperKind(raw_intent["kind"]))
    elif exact(raw_intent, {"type", "path"}) and raw_intent["type"] == "EDIT":
        path = public_path(raw_intent["path"], Stage.SESSION)
        if isinstance(path, Failure):
            return path
        intent = Edit(path)
    elif exact(raw_intent, {"type", "paths"}) and raw_intent["type"] == "CHANGE" and isinstance(raw_intent["paths"], list) and 0 < len(raw_intent["paths"]) <= MAX_FILES:
        paths = tuple(public_path(raw, Stage.SESSION) for raw in raw_intent["paths"])
        if any(isinstance(path, Failure) for path in paths) or len(set(paths)) != len(paths):
            return Failure(Stage.SESSION, Reason.INVALID_SESSION)
        intent = Change(paths)
    else:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    if not isinstance(value["files"], list) or len(value["files"]) > MAX_FILES:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    snapshots = []
    for raw in value["files"]:
        if not exact(raw, {"path", "before"}):
            return Failure(Stage.SESSION, Reason.INVALID_SESSION)
        path, before = public_path(raw["path"], Stage.SESSION), parse_state(raw["before"])
        if isinstance(path, Failure) or isinstance(before, Failure):
            return Failure(Stage.SESSION, Reason.INVALID_SESSION)
        snapshots.append(Snapshot(path, before))
    expected_paths = () if isinstance(intent, Add) else (intent.path,) if isinstance(intent, Edit) else intent.paths
    if tuple(item.path for item in snapshots) != expected_paths:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    repository = Repository(home, home / ".cfg", value["head"], value["ref"], value["index_sha256"], value["index_mode"])
    session = Session(identifier, directory, repository, intent, tuple(snapshots))
    for item in snapshots:
        if isinstance(item.before, RegularFile):
            source = directory / "preimages" / str(item.path)
            rejected = safe_leaf(directory, source, Stage.SESSION)
            if isinstance(rejected, Failure) or not source.is_file() or digest(source.read_bytes()) != item.before.sha256:
                return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    return session, refined_receipt


def parse_proposal(session: Session) -> Proposal | Failure:
    proposal_file = session.workspace / "proposal.json"
    if not proposal_file.exists():
        return Failure(Stage.PROPOSAL, Reason.NO_FINISH_INTENT)
    value = read_json(proposal_file, Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    if isinstance(value, Failure) or not exact(value, {"type", "summary", "paths", "checks"}) or value["type"] != "CHANGESET":
        return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    if not isinstance(value["summary"], str) or not 1 <= len(value["summary"]) <= 120 or any(ord(c) < 32 for c in value["summary"]):
        return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    if not isinstance(value["paths"], list) or not 0 < len(value["paths"]) <= MAX_FILES:
        return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    paths = tuple(public_path(raw, Stage.PROPOSAL) for raw in value["paths"])
    if any(isinstance(path, Failure) for path in paths) or len(set(paths)) != len(paths):
        return Failure(Stage.PROPOSAL, Reason.OUT_OF_SCOPE)
    match session.intent:
        case Add(name, kind):
            if len(paths) != 1 or kind_of(paths[0]) not in {Kind.COMMAND, Kind.FUNCTION} or name and paths[0].relative.name != name or kind != HelperKind.ANY and kind_of(paths[0]).value != kind.value:
                return Failure(Stage.PROPOSAL, Reason.OUT_OF_SCOPE)
            rejected = collision(session.repository.home, paths[0].relative.name)
            if isinstance(rejected, Failure):
                return rejected
        case Edit(path):
            if paths != (path,):
                return Failure(Stage.PROPOSAL, Reason.OUT_OF_SCOPE)
        case Change(selected):
            if not set(paths) <= set(selected):
                return Failure(Stage.PROPOSAL, Reason.OUT_OF_SCOPE)
    if not isinstance(value["checks"], list) or len(value["checks"]) > MAX_FILES * 4:
        return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    checks = []
    for raw in value["checks"]:
        if not exact(raw, {"type", "path", "args", "expect"}) or not isinstance(raw["type"], str) or raw["type"] not in {"COMMAND_SMOKE", "FUNCTION_SMOKE"}:
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
        path = public_path(raw["path"], Stage.PROPOSAL)
        if isinstance(path, Failure) or path not in paths or kind_of(path).value + "_SMOKE" != raw["type"]:
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
        if not isinstance(raw["args"], list) or len(raw["args"]) > 16 or any(not isinstance(arg, str) or len(arg) > 512 or "\0" in arg for arg in raw["args"]):
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
        expectation = raw["expect"]
        if not isinstance(expectation, dict) or type(expectation.get("code")) is not int or not 0 <= expectation["code"] <= 125:
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
        if exact(expectation, {"type", "code"}) and expectation["type"] == "EXIT":
            expected = ExpectExit(expectation["code"])
        elif exact(expectation, {"type", "code", "contains"}) and expectation["type"] == "STDOUT" and isinstance(expectation["contains"], str) and 0 < len(expectation["contains"]) <= 512:
            expected = ExpectStdout(expectation["code"], expectation["contains"])
        else:
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
        checks.append(Smoke(kind_of(path), path, tuple(raw["args"]), expected))
    for path in paths:
        if kind_of(path) in {Kind.COMMAND, Kind.FUNCTION} and not any(check.path == path for check in checks):
            return Failure(Stage.PROPOSAL, Reason.INVALID_PROPOSAL)
    return Proposal(value["summary"], paths, tuple(checks))


def revalidate(session: Session) -> Passed | Failure:
    current = active_repository(session.repository.home)
    if isinstance(current, Failure):
        return current
    if current != session.repository:
        return Failure(Stage.PREFLIGHT, Reason.DRIFT)
    for item in session.snapshots:
        target = session.repository.home / str(item.path)
        rejected = safe_leaf(session.repository.home, target)
        if isinstance(rejected, Failure):
            return rejected
        state = fingerprint(target, Stage.PREFLIGHT)
        if state != item.before:
            return Failure(Stage.PREFLIGHT, Reason.DRIFT, (str(item.path),))
    return PASSED


def candidates(session: Session, proposal: Proposal) -> tuple[Candidate, ...] | Failure:
    root = session.workspace / "candidates"
    if root.is_symlink() or not root.is_dir():
        return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
    initial = {item.path: item.before for item in session.snapshots}
    found = []
    seen = set()
    entries = 0
    total = 0
    # Only the private staged tree is enumerated. Every entry is bounded and checked.
    for current, directories, files in os.walk(root, followlinks=False):
        entries += len(directories) + len(files)
        if entries > MAX_FILES * 8:
            return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
        for name in directories:
            directory = Path(current) / name
            if directory.is_symlink():
                return Failure(Stage.PROPOSAL, Reason.UNSAFE_PATH)
        if len(directories) + len(files) > MAX_FILES * 8:
            return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
        for name in files:
            source = Path(current) / name
            parsed = public_path(source.relative_to(root).as_posix(), Stage.PROPOSAL)
            if isinstance(parsed, Failure):
                return parsed
            seen.add(parsed)
            rejected = safe_leaf(root, source, Stage.PROPOSAL)
            if isinstance(rejected, Failure):
                return rejected
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            total += info.st_size
            if total > MAX_TOTAL_BYTES or len(found) >= MAX_FILES:
                return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            before = initial.get(parsed, Absent())
            mode = stat.S_IMODE(info.st_mode)
            expected_mode = 0o755 if kind_of(parsed) == Kind.COMMAND else before.mode if isinstance(before, RegularFile) else 0o644
            if mode != expected_mode:
                return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            data = source.read_bytes()
            if b"\0" in data:
                return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            if kind_of(parsed) in {Kind.COMMAND, Kind.FUNCTION} and parsed in proposal.paths:
                for header in ("description", "usage"):
                    if not re.search(r"^# " + header + r": [^\r\n]{1,160}$", text, re.M):
                        return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
            state = RegularFile(digest(data), mode)
            if parsed in proposal.paths:
                if state == before:
                    return Failure(Stage.PROPOSAL, Reason.NO_CHANGES)
                found.append(Candidate(parsed, data, mode, before))
            elif state != before:
                return Failure(Stage.PROPOSAL, Reason.OUT_OF_SCOPE)
    if set(candidate.path for candidate in found) != set(proposal.paths) or any(isinstance(before, RegularFile) and path not in seen for path, before in initial.items()):
        return Failure(Stage.PROPOSAL, Reason.INVALID_CANDIDATE)
    for candidate in found:
        live = session.repository.home / str(candidate.path)
        rejected = safe_leaf(session.repository.home, live)
        if isinstance(rejected, Failure):
            return rejected
        if fingerprint(live, Stage.PREFLIGHT) != candidate.before:
            return Failure(Stage.PREFLIGHT, Reason.DRIFT)
    return tuple(found)


def bounded_process(command: list[str], env: dict[str, str], cwd: Path,
                    reason: Reason, timeout: int = 10) -> ProcessResult | Failure:
    if not shutil.which(command[0]) and not Path(command[0]).is_file():
        return Failure(Stage.VALIDATE, Reason.MISSING_TOOL)
    try:
        process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        return Failure(Stage.VALIDATE, reason)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = bytearray()
    total = 0
    deadline = time.monotonic() + timeout
    failed = False
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                failed = True
                break
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 4096)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                total += len(data)
                if total > MAX_PROCESS_BYTES:
                    failed = True
                    break
                if key.data == "stdout":
                    output.extend(data)
            if failed:
                break
        if failed:
            os.killpg(process.pid, signal.SIGKILL)
        code = process.wait(timeout=2)
        return Failure(Stage.VALIDATE, reason) if failed else ProcessResult(code, bytes(output))
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return Failure(Stage.VALIDATE, reason)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def checked(command: list[str], env: dict[str, str], cwd: Path, reason: Reason) -> Passed | Failure:
    result = bounded_process(command, env, cwd, reason)
    if isinstance(result, Failure):
        return result
    return PASSED if result.code == 0 else Failure(Stage.VALIDATE, reason)


def validate(session: Session, proposal: Proposal, changes: tuple[Candidate, ...]) -> Passed | Failure:
    with tempfile.TemporaryDirectory(prefix="validation-", dir=session.directory) as directory:
        sandbox = Path(directory)
        validation_home = sandbox / "home"
        validation_home.mkdir()
        env = {"HOME": str(validation_home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "XDG_CONFIG_HOME": str(validation_home / ".config"), "XDG_DATA_HOME": str(validation_home / ".local/share"),
               "XDG_STATE_HOME": str(validation_home / ".local/state"), "XDG_CACHE_HOME": str(validation_home / ".cache"),
               "ZDOTDIR": str(validation_home), "LC_ALL": "C", "TERM": "dumb"}
        for candidate in changes:
            target = validation_home / str(candidate.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidate.data)
            target.chmod(candidate.mode)
        for candidate in changes:
            target = validation_home / str(candidate.path)
            kind = kind_of(candidate.path)
            text = candidate.data.decode()
            suffix = candidate.path.relative.suffix
            shell = ""
            if kind == Kind.FUNCTION or str(candidate.path) in {".zshrc", ".zprofile"} or suffix == ".zsh":
                shell = "zsh"
            elif suffix in {".sh", ".bash"}:
                shell = "bash" if suffix == ".bash" else "sh"
            elif kind == Kind.COMMAND:
                line = text.splitlines()[0] if text else ""
                interpreters = {"#!/bin/sh": "sh", "#!/usr/bin/env sh": "sh", "#!/bin/bash": "bash",
                                "#!/usr/bin/env bash": "bash", "#!/bin/zsh": "zsh", "#!/usr/bin/env zsh": "zsh",
                                "#!/usr/bin/env python3": "python", "#!/usr/bin/python3": "python"}
                shell = interpreters.get(line, "")
                if not shell:
                    return Failure(Stage.VALIDATE, Reason.UNSUPPORTED_FORMAT)
            if shell in {"zsh", "bash", "sh"}:
                result = checked([shell, "-n", str(target)], env, sandbox, Reason.SYNTAX_FAILED)
                if isinstance(result, Failure):
                    return result
                if shell in {"sh", "bash"}:
                    result = checked(["shellcheck", "--shell=" + shell, "--", str(target)], env, sandbox, Reason.LINT_FAILED)
                    if isinstance(result, Failure):
                        return result
                if kind == Kind.FUNCTION:
                    result = checked(["zsh", "-fc", 'fpath=("$1"); autoload -Uz -- "$2"; autoload +X -- "$2" || exit; (( $+functions[$2] ))',
                                      "config-shell", str(target.parent), target.name], env, sandbox, Reason.BEHAVIOR_FAILED)
                    if isinstance(result, Failure):
                        return result
            elif shell == "python" or suffix == ".py":
                try:
                    compile(candidate.data, str(candidate.path), "exec")
                except (SyntaxError, ValueError):
                    return Failure(Stage.VALIDATE, Reason.SYNTAX_FAILED)
            elif suffix == ".toml":
                try:
                    tomllib.loads(text)
                except ValueError:
                    return Failure(Stage.VALIDATE, Reason.SYNTAX_FAILED)
            elif suffix == ".json":
                try:
                    json.loads(text)
                except ValueError:
                    return Failure(Stage.VALIDATE, Reason.SYNTAX_FAILED)
            elif suffix == ".lua":
                if shutil.which("luac"):
                    command = ["luac", "-p", str(target)]
                elif shutil.which("nvim"):
                    env["SHELL_CONFIG_VALIDATE_FILE"] = str(target)
                    command = ["nvim", "--headless", "-u", "NONE", "-n", "-c",
                               'lua if not loadfile(vim.env.SHELL_CONFIG_VALIDATE_FILE) then vim.cmd("cquit 1") end', "-c", "qa!"]
                else:
                    return Failure(Stage.VALIDATE, Reason.MISSING_TOOL)
                result = checked(command, env, sandbox, Reason.SYNTAX_FAILED)
                if isinstance(result, Failure):
                    return result
            elif kind == Kind.DOCUMENT:
                pass
            elif suffix == ".conf" and str(candidate.path).startswith(".config/ghostty/"):
                # Ghostty's validator parses the supplied configuration without launching a terminal.
                result = checked(["ghostty", "+validate-config", "--config-file=" + str(target)], env, sandbox, Reason.SYNTAX_FAILED)
                if isinstance(result, Failure):
                    return result
            else:
                return Failure(Stage.VALIDATE, Reason.UNSUPPORTED_FORMAT)
            event(Stage.VALIDATE, "SYNTAX_VERIFIED", count=1)
        for check in proposal.checks:
            target = validation_home / str(check.path)
            if check.kind == Kind.COMMAND:
                command = [str(target), *check.args]
            else:
                command = ["zsh", "-fc", 'fpath=("$1"); name=$2; shift 2; autoload -Uz -- "$name"; autoload +X -- "$name" || exit; "$name" "$@"',
                           "config-shell", str(target.parent), target.name, *check.args]
            result = bounded_process(command, env, sandbox, Reason.BEHAVIOR_FAILED, timeout=5)
            if isinstance(result, Failure) or result.code != check.expect.code:
                return Failure(Stage.VALIDATE, Reason.BEHAVIOR_FAILED)
            if isinstance(check.expect, ExpectStdout) and check.expect.contains.encode() not in result.stdout:
                return Failure(Stage.VALIDATE, Reason.BEHAVIOR_FAILED)
            event(Stage.VALIDATE, "BEHAVIOR_VERIFIED", count=1)
    return PASSED


def ensure_parents(home: Path, target: Path, created: list[Path]) -> None:
    absent = []
    current = target.parent
    while current != home and not current.exists():
        absent.append(current)
        current = current.parent
    for directory in reversed(absent):
        directory.mkdir(mode=0o755)
        created.append(directory)


def rollback(session: Session, installed: list[Candidate], created: list[Path],
             new_commit: str = "") -> Passed | Failure:
    restorations = []
    for candidate in reversed(installed):
        target = session.repository.home / str(candidate.path)
        rejected = safe_leaf(session.repository.home, target, Stage.ROLLBACK)
        if isinstance(rejected, Failure):
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        current = fingerprint(target, Stage.ROLLBACK)
        expected = RegularFile(digest(candidate.data), candidate.mode)
        if current == candidate.before:
            continue
        if current != expected:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        match candidate.before:
            case Absent():
                restorations.append((candidate, b""))
            case RegularFile(sha256, _):
                original = session.directory / "preimages" / str(candidate.path)
                rejected = safe_leaf(session.directory, original, Stage.ROLLBACK)
                if isinstance(rejected, Failure) or not original.is_file() or original.stat().st_size > MAX_FILE_BYTES:
                    return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
                data = original.read_bytes()
                if digest(data) != sha256:
                    return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
                restorations.append((candidate, data))
    if new_commit:
        ref = git(session.repository.home, "symbolic-ref", "--quiet", "HEAD", stage=Stage.ROLLBACK)
        observed = git(session.repository.home, "rev-parse", "--verify", session.repository.ref, stage=Stage.ROLLBACK)
        if isinstance(ref, Failure) or isinstance(observed, Failure) or ref.stdout.decode().strip() != session.repository.ref:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        if observed.stdout.decode().strip() == new_commit:
            result = git(session.repository.home, "update-ref", session.repository.ref, session.repository.head, new_commit, stage=Stage.ROLLBACK)
            if isinstance(result, Failure):
                return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        elif observed.stdout.decode().strip() != session.repository.head:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
    for candidate, data in restorations:
        target = session.repository.home / str(candidate.path)
        if fingerprint(target, Stage.ROLLBACK) != RegularFile(digest(candidate.data), candidate.mode):
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        match candidate.before:
            case Absent():
                target.unlink()
            case RegularFile(_, mode):
                atomic_write(target, data, mode)
        if fingerprint(target, Stage.ROLLBACK) != candidate.before:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
    event(Stage.ROLLBACK, "RESTORED", session=session.identifier, count=len(installed))
    return PASSED


def transact(session: Session, proposal: Proposal, changes: tuple[Candidate, ...], authority_digest: str) -> Passed | Failure:
    home, repo = session.repository.home, session.repository.directory
    lock = repo / "index.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, session.repository.index_mode)
    except FileExistsError:
        return Failure(Stage.COMMIT, Reason.BUSY)
    installed = []
    created = []
    publication: Publication = Unpublished()
    lock_info = os.fstat(descriptor)
    try:
        original_index = (repo / "index").read_bytes()
        rejected = revalidate(session)
        if isinstance(rejected, Failure):
            return rejected
        with tempfile.TemporaryDirectory(prefix="transaction-", dir=session.directory) as directory:
            transaction = Path(directory)
            commit_index, retained_index = transaction / "commit.index", transaction / "retained.index"
            retained_index.write_bytes(original_index)
            result = git(home, "read-tree", session.repository.head, index=commit_index, stage=Stage.COMMIT)
            if isinstance(result, Failure):
                return result
            for candidate in changes:
                result = git(home, "hash-object", "-w", "--stdin", input_data=candidate.data, stage=Stage.COMMIT)
                if isinstance(result, Failure):
                    return result
                oid = result.stdout.decode().strip()
                mode = "100755" if candidate.mode & 0o111 else "100644"
                for index in (commit_index, retained_index):
                    result = git(home, "update-index", "--add", "--cacheinfo", mode, oid, str(candidate.path), index=index, stage=Stage.COMMIT)
                    if isinstance(result, Failure):
                        return result
            tree = git(home, "write-tree", index=commit_index, stage=Stage.COMMIT)
            if isinstance(tree, Failure):
                return tree
            rejected = revalidate(session)
            if isinstance(rejected, Failure):
                return rejected
            for candidate in changes:
                target = home / str(candidate.path)
                rejected = safe_leaf(home, target, Stage.INSTALL)
                if isinstance(rejected, Failure) or fingerprint(target, Stage.INSTALL) != candidate.before:
                    outcome = rollback(session, installed, created)
                    return outcome if isinstance(outcome, Failure) else Failure(Stage.INSTALL, Reason.DRIFT)
                ensure_parents(home, target, created)
                installed.append(candidate)
                atomic_write(target, candidate.data, candidate.mode)
            event(Stage.INSTALL, "INSTALLED", session=session.identifier, count=len(installed))
            signing = git(home, "config", "--bool", "--default=false", "--get", "commit.gpgsign", stage=Stage.COMMIT)
            if isinstance(signing, Failure) or signing.stdout not in {b"true\n", b"false\n"}:
                outcome = rollback(session, installed, created)
                return outcome if isinstance(outcome, Failure) else Failure(Stage.COMMIT, Reason.COMMIT_FAILED)
            signing_args = ("-S",) if signing.stdout == b"true\n" else ()
            result = git(home, "commit-tree", tree.stdout.decode().strip(), "-p", session.repository.head, *signing_args,
                         input_data=("config shell: " + proposal.summary + "\n").encode(), stage=Stage.COMMIT)
            if isinstance(result, Failure):
                outcome = rollback(session, installed, created)
                return outcome if isinstance(outcome, Failure) else result
            new_commit = result.stdout.decode().strip()
            # Files may have external writers even while Git's index is locked.
            if any(fingerprint(home / str(candidate.path), Stage.COMMIT) != RegularFile(digest(candidate.data), candidate.mode) for candidate in changes):
                return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
            current = active_repository(home)
            if current != session.repository:
                outcome = rollback(session, installed, created)
                return outcome if isinstance(outcome, Failure) else Failure(Stage.COMMIT, Reason.DRIFT)
            publication = RefAttempted(new_commit)
            event(Stage.COMMIT, "PREPARED", session=session.identifier, commit=new_commit)
            result = git(home, "update-ref", session.repository.ref, new_commit, session.repository.head, stage=Stage.COMMIT)
            if isinstance(result, Failure):
                outcome = rollback(session, installed, created, new_commit)
                return outcome if isinstance(outcome, Failure) else result
            index_data = retained_index.read_bytes()
            offset = 0
            while offset < len(index_data):
                written = os.write(descriptor, index_data[offset:])
                if written == 0:
                    raise OSError("index write made no progress")
                offset += written
            os.fsync(descriptor)
            lock.chmod(session.repository.index_mode)
            publication = IndexAttempted(new_commit)
            os.replace(lock, repo / "index")
            write_receipt(session, CommittedReceipt(authority_digest, new_commit))
            event(Stage.COMMIT, "COMMITTED", session=session.identifier, count=len(changes), commit=new_commit)
            return PASSED
    except (OSError, KeyboardInterrupt) as error:
        # After publishing the index another Git writer can proceed; retain recovery
        # evidence rather than overwrite a potentially newly owned index.
        if isinstance(publication, IndexAttempted):
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        try:
            outcome = rollback(session, installed, created, publication.commit if isinstance(publication, RefAttempted) else "")
        except OSError:
            return Failure(Stage.ROLLBACK, Reason.RECOVERY_REQUIRED)
        return outcome if isinstance(outcome, Failure) else Failure(Stage.INSTALL, Reason.INTERRUPTED if isinstance(error, KeyboardInterrupt) else Reason.IO_FAILED)
    finally:
        try:
            os.close(descriptor)
        finally:
            if os.path.lexists(lock):
                current = lock.lstat()
                if (current.st_dev, current.st_ino) == (lock_info.st_dev, lock_info.st_ino):
                    lock.unlink()


def finish_locked(home: Path, identifier: str) -> Passed | Failure:
    loaded = load_session(home, identifier)
    if isinstance(loaded, Failure):
        return loaded
    session, record = loaded
    if isinstance(record, CommittedReceipt):
        event(Stage.COMMIT, "ALREADY_COMMITTED", session=identifier, commit=record.commit)
        return PASSED
    if record.phase != SessionPhase.READY:
        return Failure(Stage.AGENT, Reason.AGENT_FAILED)
    rejected = revalidate(session)
    if isinstance(rejected, Failure):
        return rejected
    proposal = parse_proposal(session)
    if isinstance(proposal, Failure):
        return proposal
    changes = candidates(session, proposal)
    if isinstance(changes, Failure):
        return changes
    event(Stage.PROPOSAL, "ACCEPTED", session=identifier, count=len(changes))
    rejected = validate(session, proposal, changes)
    if isinstance(rejected, Failure):
        return rejected
    return transact(session, proposal, changes, record.sha256)


def session_lock(home: Path, identifier: str, operation) -> Passed | Failure:
    if not IDENTIFIER.fullmatch(identifier):
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    path = session_root(home) / identifier / "agent.lock"
    rejected = safe_leaf(home, path, Stage.SESSION)
    if isinstance(rejected, Failure):
        return rejected
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return Failure(Stage.SESSION, Reason.BUSY)
        return operation()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def finish(home: Path, identifier: str) -> Passed | Failure:
    return session_lock(home, identifier, lambda: finish_locked(home, identifier))


def launch_locked(session: Session, request: str) -> Passed | Failure:
    if not shutil.which("pi"):
        return Failure(Stage.AGENT, Reason.MISSING_TOOL)
    instructions = session.workspace / "instructions.md"
    command = ["pi", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files",
               "--append-system-prompt", str(instructions), "--name", "config shell " + session.identifier[:8]]
    if request:
        command.append(request)
    event(Stage.AGENT, "STARTED", session=session.identifier)
    try:
        result = subprocess.run(command, cwd=session.workspace, env=clean_env())
    except KeyboardInterrupt:
        result = None
    loaded = load_session(session.repository.home, session.identifier)
    if isinstance(loaded, Failure):
        return loaded
    _, record = loaded
    if not isinstance(record, PendingReceipt) or record.phase != SessionPhase.RUNNING:
        return Failure(Stage.SESSION, Reason.INVALID_SESSION)
    if result is None or result.returncode:
        write_receipt(session, PendingReceipt(SessionPhase.FAILED, record.sha256))
        return Failure(Stage.AGENT, Reason.AGENT_FAILED)
    write_receipt(session, PendingReceipt(SessionPhase.READY, record.sha256))
    event(Stage.AGENT, "EXITED", session=session.identifier)
    return PASSED


def launch(session: Session, request: str) -> Passed | Failure:
    result = session_lock(session.repository.home, session.identifier, lambda: launch_locked(session, request))
    return result if isinstance(result, Failure) else finish(session.repository.home, session.identifier)


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="config shell", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("add", help="Open Pi to create a reusable command or shell function")
    add.add_argument("--name")
    add.add_argument("--kind", choices=("command", "function"))
    add.add_argument("request", nargs="*")
    edit = commands.add_parser("edit", help="Open Pi to revise an existing managed helper")
    edit.add_argument("name")
    change = commands.add_parser("change", help="Open Pi to change selected public configuration")
    change.add_argument("request", nargs="+")
    change.add_argument("--paths", nargs="+", help="Explicit HOME-relative configuration paths")
    final = commands.add_parser("finish", help="Validate and commit a prepared successful Pi session")
    final.add_argument("session")
    try:
        args = parser.parse_args(argv)
        home = Path(os.environ.get("HOME", "")).absolute()
        if args.action == "finish":
            result = finish(home, args.session)
        else:
            repository = active_repository(home)
            if isinstance(repository, Failure):
                result = repository
            else:
                intent = selected_intent(home, args)
                if isinstance(intent, Failure):
                    result = intent
                else:
                    request = " ".join(args.request) if hasattr(args, "request") else "Revise the existing helper " + args.name
                    session = prepare(repository, intent, request)
                    result = session if isinstance(session, Failure) else launch(session, request)
    except KeyboardInterrupt:
        result = Failure(Stage.SESSION, Reason.INTERRUPTED)
    except (OSError, ValueError, UnicodeError):
        result = Failure(Stage.SESSION, Reason.IO_FAILED)
    if isinstance(result, Failure):
        print(f"config-shell: stage={result.stage.value} outcome={result.reason.value}", file=sys.stderr)
        return 2
    return 0


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def main(argv: list[str]) -> int:
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        return run(argv)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""An on-demand work picker with scoped accounts and owned review worktrees."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid


class Stage(Enum):
    CONFIG = "config"
    ACCOUNT = "account"
    READ = "read"
    CREATE = "create"
    REVIEW = "review"
    CLEANUP = "cleanup"
    PICKER = "picker"


class Reason(Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    IO_FAILED = "IO_FAILED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    COMMAND_FAILED = "COMMAND_FAILED"
    TIMED_OUT = "TIMED_OUT"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    ACCOUNT_REQUIRED = "ACCOUNT_REQUIRED"
    ACCOUNT_AMBIGUOUS = "ACCOUNT_AMBIGUOUS"
    ACCOUNT_UNKNOWN = "ACCOUNT_UNKNOWN"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INVALID_PR = "INVALID_PR"
    REPOSITORY_REQUIRED = "REPOSITORY_REQUIRED"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    UNSAFE_PATH = "UNSAFE_PATH"
    OWNERSHIP_UNPROVEN = "OWNERSHIP_UNPROVEN"
    HEAD_CHANGED = "HEAD_CHANGED"
    WORKTREE_DIRTY = "WORKTREE_DIRTY"
    CREATE_UNVERIFIED = "CREATE_UNVERIFIED"
    REMOVE_UNVERIFIED = "REMOVE_UNVERIFIED"
    TTY_REQUIRED = "TTY_REQUIRED"
    INPUT_REQUIRED = "INPUT_REQUIRED"


@dataclass(frozen=True)
class Failure:
    stage: Stage
    reason: Reason
    context: str = ""


@dataclass(frozen=True)
class Hostname:
    value: str

    @property
    def token_variable(self) -> str:
        # gh documents github.com and ghe.com subdomains as Cloud token hosts.
        return "GH_TOKEN" if self.value == "github.com" or self.value.endswith(".ghe.com") else "GH_ENTERPRISE_TOKEN"


@dataclass(frozen=True)
class Account:
    name: str
    hostname: Hostname
    login: str


@dataclass(frozen=True)
class Repository:
    hostname: Hostname
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def gh_name(self) -> str:
        return f"{self.hostname.value}/{self.full_name}"


@dataclass(frozen=True)
class Mapping:
    repository: Repository
    path: Path


@dataclass(frozen=True)
class Config:
    accounts: tuple[Account, ...]
    repositories: tuple[Mapping, ...]
    project: str
    editor: tuple[str, ...]


class Queue(Enum):
    AUTHORED = "authored"
    REVIEW_REQUESTED = "review_requested"


class CheckState(Enum):
    PASSING = "PASSING"
    FAILING = "FAILING"
    PENDING = "PENDING"
    UNVERIFIED = "UNVERIFIED"


class ReviewState(Enum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class PullRequest:
    account: Account
    repository: Repository
    number: int

    @property
    def url(self) -> str:
        return f"https://{self.repository.hostname.value}/{self.repository.full_name}/pull/{self.number}"


@dataclass(frozen=True)
class PrItem:
    pr: PullRequest
    title: str
    queue: Queue
    draft: bool


@dataclass(frozen=True)
class JiraItem:
    key: str
    title: str
    status: str


@dataclass(frozen=True)
class PrDetails:
    pr: PullRequest
    title: str
    body: str
    head: str
    checks: CheckState
    review: ReviewState


@dataclass(frozen=True)
class TicketLink:
    url: str


@dataclass(frozen=True)
class NoTicketLink:
    pass


@dataclass(frozen=True)
class CreatedTicket:
    key: str
    identifier: str
    link: TicketLink | NoTicketLink


Item = PrItem | JiraItem


@dataclass(frozen=True)
class SourceSuccess:
    name: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class SourceFailure:
    name: str
    failure: Failure


Source = SourceSuccess | SourceFailure


@dataclass(frozen=True)
class Completed:
    stdout: str


@dataclass(frozen=True)
class VerifiedGh:
    account: Account
    token: str = field(repr=False)

    def environment(self) -> dict[str, str]:
        env = clean_environment()
        env["GH_HOST"] = self.account.hostname.value
        env[self.account.hostname.token_variable] = self.token
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": os.devnull})
        return env


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str
    detached: bool


@dataclass(frozen=True)
class Session:
    identifier: str
    path: Path
    repository: Path
    common: Path
    git_directory: Path
    head: str
    pr: PullRequest


@dataclass(frozen=True)
class ReviewManaged:
    pass


@dataclass(frozen=True)
class ReviewRemoved:
    pass


@dataclass(frozen=True)
class ReviewPreserved:
    failure: Failure


ReviewLifecycle = ReviewManaged | ReviewRemoved | ReviewPreserved


@dataclass(frozen=True)
class Action:
    key: str
    name: str
    description: str
    example: str


ACTIONS = (
    Action("enter", "open", "Open the selected PR or Jira ticket", "work"),
    Action("r", "review", "Open a PR in an owned detached review worktree", "work review <PR URL> --account <name>"),
    Action("n", "new", "Create a Jira ticket from a reviewed summary", "work new"),
    Action("o", "open", "Open the selected PR or Jira ticket", "work"),
    Action("ctrl-r", "refresh", "Refresh account PRs and assigned Jira tickets", "work list"),
    Action("?", "help", "Show this shortcut reference", "keys work"),
    Action("q", "quit", "Leave the work panel", "work"),
)


def shortcuts() -> list[dict[str, str]]:
    return [{"key": action.key, "description": action.description, "example": action.example}
            for action in ACTIONS]


def clean_environment() -> dict[str, str]:
    hidden = {"GH_HOST", "GH_REPO", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG"}
    return {key: value for key, value in os.environ.items()
            if key not in hidden and not key.startswith("GIT_")}


def command(args: list[str], stage: Stage, *, cwd: Path | None = None,
            env: dict[str, str] | None = None, timeout: int = 30) -> Completed | Failure:
    """Capture external evidence; never expose raw stderr, argv, or credentials."""
    try:
        run = subprocess.run(args, cwd=cwd, env=env if env is not None else clean_environment(),
                             text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return Failure(stage, Reason.TOOL_UNAVAILABLE, Path(args[0]).name)
    except subprocess.TimeoutExpired:
        return Failure(stage, Reason.TIMED_OUT, Path(args[0]).name)
    except (OSError, UnicodeError):
        return Failure(stage, Reason.IO_FAILED, Path(args[0]).name)
    if run.returncode:
        return Failure(stage, Reason.COMMAND_FAILED, Path(args[0]).name)
    if len(run.stdout) > 2_000_000:
        return Failure(stage, Reason.INVALID_OUTPUT, Path(args[0]).name)
    return Completed(run.stdout)


def git(path: Path, *args: str, stage: Stage = Stage.REVIEW) -> Completed | Failure:
    return command(["git", "-C", str(path), *args], stage)


def decoded(raw: str, stage: Stage) -> object | Failure:
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return Failure(stage, Reason.INVALID_OUTPUT)


def text(value: object, limit: int = 500) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def hostname(value: object) -> Hostname | Failure:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    if any(not part or part.startswith("-") or part.endswith("-") for part in value.split(".")):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    return Hostname(value)


def account(value: object) -> Account | Failure:
    if not isinstance(value, dict) or set(value) != {"name", "hostname", "login"}:
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    host = hostname(value["hostname"])
    if isinstance(host, Failure) or not isinstance(value["name"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value["name"]):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    if not isinstance(value["login"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", value["login"]):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    return Account(value["name"], host, value["login"])


def repository(host: Hostname, full_name: object) -> Repository | Failure:
    if not isinstance(full_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    owner, name = full_name.split("/")
    if owner in (".", "..") or name in (".", ".."):
        return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    return Repository(host, owner, name)


def config_from(value: object) -> Config | Failure:
    if not isinstance(value, dict) or set(value) != {"type", "version", "accounts", "repositories", "jira", "editor"} or value["type"] != "WORK_CONFIG" or type(value["version"]) is not int or value["version"] != 1:
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    if not isinstance(value["accounts"], list) or not isinstance(value["repositories"], list):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    accounts = tuple(account(entry) for entry in value["accounts"])
    if any(isinstance(entry, Failure) for entry in accounts):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    if len({entry.name for entry in accounts}) != len(accounts) or len({(entry.hostname, entry.login) for entry in accounts}) != len(accounts):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    mappings = []
    for entry in value["repositories"]:
        if not isinstance(entry, dict) or set(entry) != {"hostname", "owner", "name", "path"}:
            return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
        host = hostname(entry["hostname"])
        if isinstance(host, Failure) or not text(entry["path"], 4096) or not text(entry["owner"], 100) or not text(entry["name"], 100):
            return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
        repo = repository(host, f"{entry['owner']}/{entry['name']}")
        if isinstance(repo, Failure):
            return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
        path = Path(entry["path"]).expanduser()
        if not path.is_absolute():
            return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
        mappings.append(Mapping(repo, path))
    if len({entry.repository for entry in mappings}) != len(mappings):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    jira = value["jira"]
    if not isinstance(jira, dict) or set(jira) != {"project"} or not isinstance(jira["project"], str) or (jira["project"] and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", jira["project"])):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    editor = value["editor"]
    if not isinstance(editor, list) or not editor or not all(text(part, 4096) for part in editor):
        return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
    return Config(accounts, tuple(mappings), jira["project"], tuple(editor))


def read_json(path: Path, stage: Stage) -> object | Failure:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            return Failure(stage, Reason.UNSAFE_PATH)
        return decoded(path.read_text(), stage)
    except (OSError, UnicodeError):
        return Failure(stage, Reason.IO_FAILED)


def load_config(path: Path) -> Config | Failure:
    value = read_json(path, Stage.CONFIG)
    if isinstance(value, Failure):
        return value
    checked = config_from(value)
    if isinstance(checked, Failure):
        return checked
    local = path.with_name("config.local.json")
    if local.exists() or local.is_symlink():
        overlay = read_json(local, Stage.CONFIG)
        if isinstance(overlay, Failure):
            return overlay
        if not isinstance(overlay, dict) or overlay.get("type") != "WORK_CONFIG_LOCAL" or not set(overlay).issubset({"type", "accounts", "repositories", "jira"}):
            return Failure(Stage.CONFIG, Reason.INVALID_CONFIG)
        value = {**value, **{key: data for key, data in overlay.items() if key != "type"}}
        return config_from(value)
    return checked


def discover_accounts() -> tuple[Account, ...] | Failure:
    run = command(["gh", "auth", "status", "--json", "hosts"], Stage.ACCOUNT)
    if isinstance(run, Failure):
        return run
    value = decoded(run.stdout, Stage.ACCOUNT)
    if not isinstance(value, dict) or set(value) != {"hosts"} or not isinstance(value["hosts"], dict):
        return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
    found = []
    for host_value, entries in value["hosts"].items():
        host = hostname(host_value)
        if isinstance(host, Failure) or not isinstance(entries, list):
            return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
        for entry in entries:
            # gh's auth object may include masked tokens. Only these two identity
            # fields cross into the domain or ever become output.
            if not isinstance(entry, dict) or not isinstance(entry.get("login"), str):
                return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
            login = entry["login"]
            name = re.sub(r"[^a-z0-9_-]", "-", f"{host.value}-{login}".lower())
            refined = account({"name": name, "hostname": host.value, "login": login})
            if isinstance(refined, Failure):
                return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
            found.append(refined)
    if len({entry.name for entry in found}) != len(found):
        return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
    return tuple(found)


def accounts_for(config: Config) -> tuple[Account, ...] | Failure:
    if config.accounts:
        return config.accounts
    found = discover_accounts()
    if isinstance(found, Failure):
        return found
    return found if found else Failure(Stage.ACCOUNT, Reason.ACCOUNT_REQUIRED)


def verified_gh(selected: Account) -> VerifiedGh | Failure:
    result = command(["gh", "auth", "token", "--hostname", selected.hostname.value,
                      "--user", selected.login], Stage.ACCOUNT)
    if isinstance(result, Failure):
        return Failure(result.stage, result.reason, selected.name)
    token = result.stdout.strip()
    if not token or len(token) > 1000 or any(c.isspace() or ord(c) < 32 for c in token):
        return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT, selected.name)
    scoped = VerifiedGh(selected, token)
    identity = command(["gh", "api", "user", "--hostname", selected.hostname.value,
                        "--jq", ".login"], Stage.ACCOUNT, env=scoped.environment())
    if isinstance(identity, Failure):
        return Failure(identity.stage, identity.reason, selected.name)
    if identity.stdout.strip().casefold() != selected.login.casefold():
        return Failure(Stage.ACCOUNT, Reason.IDENTITY_MISMATCH, selected.name)
    return scoped


def parse_pr_url(value: str) -> tuple[Repository, int] | Failure:
    try:
        url = urlsplit(value)
    except ValueError:
        return Failure(Stage.REVIEW, Reason.INVALID_PR)
    host = hostname(url.hostname)
    match = re.fullmatch(r"/([^/]+)/([^/]+)/pull/([1-9][0-9]*)/?", url.path)
    if isinstance(host, Failure) or url.scheme != "https" or url.netloc != host.value or url.query or url.fragment or not match:
        return Failure(Stage.REVIEW, Reason.INVALID_PR)
    repo = repository(host, f"{match[1]}/{match[2]}")
    if isinstance(repo, Failure):
        return Failure(Stage.REVIEW, Reason.INVALID_PR)
    return repo, int(match[3])


def pr_items(raw: str, selected: Account, queue: Queue) -> tuple[PrItem, ...] | Failure:
    value = decoded(raw, Stage.READ)
    if not isinstance(value, list) or len(value) > 50:
        return Failure(Stage.READ, Reason.INVALID_OUTPUT, selected.name)
    rows = []
    fields = {"number", "title", "url", "repository", "updatedAt", "isDraft"}
    for row in value:
        if not isinstance(row, dict) or set(row) != fields or type(row["number"]) is not int or row["number"] <= 0 or type(row["isDraft"]) is not bool or not text(row["title"]):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT, selected.name)
        nested = row["repository"]
        if not isinstance(nested, dict):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT, selected.name)
        repo = repository(selected.hostname, nested.get("nameWithOwner"))
        parsed = parse_pr_url(row["url"]) if isinstance(row["url"], str) else Failure(Stage.READ, Reason.INVALID_OUTPUT)
        if isinstance(repo, Failure) or isinstance(parsed, Failure) or parsed != (repo, row["number"]):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT, selected.name)
        rows.append(PrItem(PullRequest(selected, repo, row["number"]), row["title"], queue, row["isDraft"]))
    return tuple(rows)


def jira_items(raw: str) -> tuple[JiraItem, ...] | Failure:
    value = decoded(raw, Stage.READ)
    # ACLI projects the Jira issue array. Unsupported envelopes or field
    # representations fail visibly rather than becoming an empty result.
    if not isinstance(value, list) or len(value) > 50:
        return Failure(Stage.READ, Reason.INVALID_OUTPUT, "jira")
    rows = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", row["key"]):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT, "jira")
        fields = row.get("fields")
        status = fields.get("status") if isinstance(fields, dict) else None
        if not isinstance(fields, dict) or not isinstance(status, dict) or not text(fields.get("summary")) or not text(status.get("name"), 100):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT, "jira")
        rows.append(JiraItem(row["key"], fields["summary"], status["name"]))
    return tuple(rows)


def collect(config: Config) -> tuple[Source, ...]:
    sources: list[Source] = []
    selected = accounts_for(config)
    if isinstance(selected, Failure):
        sources.append(SourceFailure("github", selected))
    else:
        for identity in selected:
            scoped = verified_gh(identity)
            if isinstance(scoped, Failure):
                sources.append(SourceFailure(identity.name, scoped))
                continue
            for queue, flag in ((Queue.AUTHORED, "--author"), (Queue.REVIEW_REQUESTED, "--review-requested")):
                name = f"{identity.name}:{queue.value}"
                result = command(["gh", "search", "prs", flag, "@me", "--state", "open", "--limit", "50",
                                  "--json", "number,title,url,repository,updatedAt,isDraft"], Stage.READ, env=scoped.environment())
                rows = result if isinstance(result, Failure) else pr_items(result.stdout, identity, queue)
                sources.append(SourceFailure(name, rows) if isinstance(rows, Failure) else SourceSuccess(name, rows))
    jql = "assignee = currentUser() AND statusCategory != Done"
    if config.project:
        jql += f' AND project = "{config.project}"'
    result = command(["acli", "jira", "workitem", "search", "--jql", jql + " ORDER BY updated DESC",
                      "--fields", "key,summary,status", "--limit", "50", "--json"], Stage.READ)
    rows = result if isinstance(result, Failure) else jira_items(result.stdout)
    sources.append(SourceFailure("jira", rows) if isinstance(rows, Failure) else SourceSuccess("jira", rows))
    return tuple(sources)


def item_json(item: Item) -> dict:
    match item:
        case PrItem(pr, title, queue, draft):
            return {"type": "PULL_REQUEST", "account": pr.account.name, "hostname": pr.account.hostname.value,
                    "repository": pr.repository.full_name, "number": pr.number, "title": title,
                    "url": pr.url, "queue": queue.value, "draft": draft,
                    "checks": "UNVERIFIED", "review": "UNVERIFIED"}
        case JiraItem(key, title, status):
            return {"type": "JIRA_TICKET", "key": key, "title": title, "status": status}


def failure_json(failure: Failure) -> dict[str, str]:
    return {"type": "WORK_FAILURE", "stage": failure.stage.value, "reason": failure.reason.value, "context": failure.context}


def snapshot(sources: tuple[Source, ...]) -> dict:
    items = [item_json(item) for source in sources if isinstance(source, SourceSuccess) for item in source.items]
    statuses = [{"type": "SOURCE_SUCCESS", "name": source.name, "count": len(source.items)} if isinstance(source, SourceSuccess)
                else {"type": "SOURCE_FAILURE", "name": source.name, "failure": failure_json(source.failure)} for source in sources]
    return {"type": "WORK_SNAPSHOT", "items": items, "sources": statuses}


def report(failure: Failure) -> int:
    print(f"work: stage={failure.stage.value} outcome={failure.reason.value}" +
          (f" context={failure.context}" if failure.context else ""), file=sys.stderr)
    return 1


def list_work(config: Config, as_json: bool) -> int:
    sources = collect(config)
    if as_json:
        print(json.dumps(snapshot(sources)))
    else:
        for source in sources:
            if isinstance(source, SourceFailure):
                print(f"! {source.name}: {source.failure.reason.value}")
                continue
            print(f"{source.name} ({len(source.items)})")
            for item in source.items:
                match item:
                    case PrItem(pr, title, queue, draft):
                        print(f"  {pr.repository.full_name} #{pr.number}  {title}")
                    case JiraItem(key, title, status):
                        print(f"  {key} [{status}]  {title}")
    return int(any(isinstance(source, SourceFailure) for source in sources))


def safe_state(path: Path) -> Failure | None:
    if not path.is_absolute():
        return Failure(Stage.CONFIG, Reason.UNSAFE_PATH)
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            return Failure(Stage.CONFIG, Reason.UNSAFE_PATH)
    return None


def ensure_state(path: Path) -> Failure | None:
    rejected = safe_state(path)
    if rejected:
        return rejected
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        (path / "sessions").mkdir(mode=0o700, exist_ok=True)
        (path / "reviews").mkdir(mode=0o700, exist_ok=True)
    except OSError:
        return Failure(Stage.REVIEW, Reason.IO_FAILED)
    return safe_state(path / "sessions") or safe_state(path / "reviews")


def write_private(path: Path, value: dict, *, exclusive: bool = False) -> Failure | None:
    try:
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return Failure(Stage.REVIEW, Reason.IO_FAILED)
    return None


def choose_account(config: Config, host: Hostname, name: str | None, state: Path) -> Account | Failure:
    identities = accounts_for(config)
    if isinstance(identities, Failure):
        return identities
    if name is None and (state / "account.json").exists():
        stored = read_json(state / "account.json", Stage.ACCOUNT)
        if not isinstance(stored, dict) or set(stored) != {"type", "version", "account"} or stored["type"] != "WORK_ACCOUNT_SELECTION" or type(stored["version"]) is not int or stored["version"] != 1 or not text(stored["account"], 100):
            return Failure(Stage.ACCOUNT, Reason.INVALID_OUTPUT)
        name = stored["account"]
    candidates = tuple(entry for entry in identities if entry.hostname == host and (name is None or entry.name == name))
    if not candidates:
        return Failure(Stage.ACCOUNT, Reason.ACCOUNT_UNKNOWN if name else Reason.ACCOUNT_REQUIRED)
    if len(candidates) != 1:
        return Failure(Stage.ACCOUNT, Reason.ACCOUNT_AMBIGUOUS)
    return candidates[0]


def origin_repository(path: Path) -> tuple[Path, Repository] | Failure:
    root = git(path, "rev-parse", "--show-toplevel")
    if isinstance(root, Failure):
        return Failure(Stage.REVIEW, Reason.REPOSITORY_REQUIRED)
    root_path = Path(root.stdout.strip()).resolve()
    remote = git(root_path, "remote", "get-url", "origin")
    if isinstance(remote, Failure):
        return Failure(Stage.REVIEW, Reason.REPOSITORY_REQUIRED)
    raw = remote.stdout.strip()
    ssh = re.fullmatch(r"git@([^:]+):([^/]+/[^/]+?)(?:\.git)?", raw)
    if ssh:
        host_value, name = ssh[1], ssh[2]
    else:
        try:
            url = urlsplit(raw)
        except ValueError:
            return Failure(Stage.REVIEW, Reason.REPOSITORY_MISMATCH)
        if url.scheme not in ("https", "ssh") or url.query or url.fragment or url.password or (url.scheme == "https" and url.username) or (url.scheme == "ssh" and url.username not in (None, "git")):
            return Failure(Stage.REVIEW, Reason.REPOSITORY_MISMATCH)
        host_value, name = url.hostname, url.path.lstrip("/").removesuffix(".git")
    host = hostname(host_value)
    repo = repository(host, name) if isinstance(host, Hostname) else host
    if isinstance(repo, Failure):
        return Failure(Stage.REVIEW, Reason.REPOSITORY_MISMATCH)
    return root_path, repo


def resolve_review(config: Config, target: str, account_name: str | None, repo_path: str | None,
                   state: Path) -> tuple[PullRequest, Path] | Failure:
    if target.isdecimal() and int(target) > 0:
        local = origin_repository(Path(repo_path).expanduser() if repo_path else Path.cwd())
        if isinstance(local, Failure):
            return local
        path, repo = local
        number = int(target)
    else:
        parsed = parse_pr_url(target)
        if isinstance(parsed, Failure):
            return parsed
        repo, number = parsed
        mapped = next((entry.path for entry in config.repositories if entry.repository == repo), Path.cwd())
        local = origin_repository(Path(repo_path).expanduser() if repo_path else mapped)
        if isinstance(local, Failure):
            return local
        path, observed = local
        if observed != repo:
            return Failure(Stage.REVIEW, Reason.REPOSITORY_MISMATCH)
    selected = choose_account(config, repo.hostname, account_name, state)
    return selected if isinstance(selected, Failure) else (PullRequest(selected, repo, number), path)


def worktrees(path: Path, stage: Stage) -> tuple[Worktree, ...] | Failure:
    result = git(path, "worktree", "list", "--porcelain", "-z", stage=stage)
    if isinstance(result, Failure):
        return result
    found = []
    for record in result.stdout.split("\0\0"):
        if not record.strip("\0"):
            continue
        fields = record.strip("\0").split("\0")
        if not fields[0].startswith("worktree "):
            return Failure(stage, Reason.INVALID_OUTPUT)
        actual = Path(fields[0][9:])
        heads = [entry[5:] for entry in fields if entry.startswith("HEAD ")]
        if "bare" in fields:
            continue
        if len(heads) != 1 or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", heads[0]):
            return Failure(stage, Reason.INVALID_OUTPUT)
        found.append(Worktree(actual, heads[0], "detached" in fields))
    return tuple(found)


def common_directory(path: Path, stage: Stage) -> Path | Failure:
    result = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", stage=stage)
    if isinstance(result, Failure):
        return result
    common = Path(result.stdout.strip())
    return common.resolve() if common.is_absolute() else Failure(stage, Reason.INVALID_OUTPUT)


def git_directory(path: Path, stage: Stage) -> Path | Failure:
    result = git(path, "rev-parse", "--absolute-git-dir", stage=stage)
    if isinstance(result, Failure):
        return result
    directory = Path(result.stdout.strip())
    return directory.resolve() if directory.is_absolute() else Failure(stage, Reason.INVALID_OUTPUT)


def session_json(session: Session) -> dict:
    return {"type": "WORK_REVIEW_SESSION", "version": 1, "session": session.identifier, "path": str(session.path),
            "repository": str(session.repository), "common": str(session.common), "gitDirectory": str(session.git_directory), "head": session.head,
            "url": session.pr.url, "account": {"name": session.pr.account.name,
                "hostname": session.pr.account.hostname.value, "login": session.pr.account.login}}


def session_from(value: object, state: Path, identifier: str) -> Session | Failure:
    fields = {"type", "version", "session", "path", "repository", "common", "gitDirectory", "head", "url", "account"}
    if not isinstance(value, dict) or set(value) != fields or value["type"] != "WORK_REVIEW_SESSION" or type(value["version"]) is not int or value["version"] != 1 or value["session"] != identifier:
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    if not all(text(value[name], 4096) for name in ("path", "repository", "common", "gitDirectory", "head", "url")) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["head"]):
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    path, repo, common, administrative = (Path(value[name]) for name in ("path", "repository", "common", "gitDirectory"))
    if path != state / "reviews" / identifier or not repo.is_absolute() or not common.is_absolute() or administrative.parent != common / "worktrees":
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    selected = account(value["account"])
    parsed = parse_pr_url(value["url"])
    if isinstance(selected, Failure) or isinstance(parsed, Failure) or selected.hostname != parsed[0].hostname:
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    return Session(identifier, path, repo, common, administrative, value["head"], PullRequest(selected, parsed[0], parsed[1]))


def review(config: Config, target: str, account_name: str | None, repo_path: str | None,
           state: Path, as_json: bool = False, *, open_editor: bool = True) -> int:
    resolved = resolve_review(config, target, account_name, repo_path, state)
    if isinstance(resolved, Failure):
        return report(resolved)
    pr, repo = resolved
    scoped = verified_gh(pr.account)
    if isinstance(scoped, Failure):
        return report(scoped)
    metadata = command(["gh", "pr", "view", str(pr.number), "--repo", pr.repository.gh_name,
                        "--json", "number,title,url,headRefOid,state"], Stage.REVIEW, env=scoped.environment())
    if isinstance(metadata, Failure):
        return report(metadata)
    value = decoded(metadata.stdout, Stage.REVIEW)
    if not isinstance(value, dict) or set(value) != {"number", "title", "url", "headRefOid", "state"} or type(value["number"]) is not int or value["number"] != pr.number or not text(value["title"]) or value["url"] != pr.url or value["state"] != "OPEN" or not isinstance(value["headRefOid"], str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["headRefOid"]):
        return report(Failure(Stage.REVIEW, Reason.INVALID_OUTPUT))
    common = common_directory(repo, Stage.REVIEW)
    if isinstance(common, Failure):
        return report(common)
    rejected = ensure_state(state)
    if rejected:
        return report(rejected)
    identifier = str(uuid.uuid4())
    path = state / "reviews" / identifier
    if path.exists() or path.is_symlink():
        return report(Failure(Stage.REVIEW, Reason.UNSAFE_PATH))
    created = command(["gh", "pr", "checkout", str(pr.number), "--repo", pr.repository.gh_name,
                       "--detach", "--worktree", str(path)], Stage.REVIEW, cwd=repo,
                      env=scoped.environment(), timeout=120)
    if isinstance(created, Failure):
        # A failed external command can leave partial state. Preserve that path.
        return report(Failure(Stage.REVIEW, Reason.CREATE_UNVERIFIED, identifier))
    registered = worktrees(repo, Stage.REVIEW)
    actual_common = common_directory(path, Stage.REVIEW)
    administrative = git_directory(path, Stage.REVIEW)
    match = next((entry for entry in registered if entry.path == path), None) if isinstance(registered, tuple) else None
    if match is None or not match.detached or match.head != value["headRefOid"] or actual_common != common or isinstance(administrative, Failure) or administrative.parent != common / "worktrees":
        return report(Failure(Stage.REVIEW, Reason.CREATE_UNVERIFIED, identifier))
    owned = Session(identifier, path, repo, common, administrative, match.head, pr)
    rejected = write_private(administrative / "shell-config-work-session.json", {"type": "WORKTREE_OWNERSHIP", "session": identifier}, exclusive=True)
    if not rejected:
        rejected = write_private(state / "sessions" / f"{identifier}.json", session_json(owned), exclusive=True)
    if rejected:
        return report(Failure(Stage.REVIEW, Reason.CREATE_UNVERIFIED, identifier))
    lifecycle: ReviewLifecycle = ReviewManaged()
    if open_editor:
        if not as_json:
            print(f"session={identifier} path={path}", flush=True)
        try:
            run = subprocess.run([*config.editor, str(path)], cwd=path, env=clean_environment())
        except OSError:
            return review_result(owned, ReviewPreserved(Failure(Stage.REVIEW, Reason.TOOL_UNAVAILABLE)), as_json)
        if run.returncode:
            return review_result(owned, ReviewPreserved(Failure(Stage.REVIEW, Reason.COMMAND_FAILED)), as_json)
        removed = remove_review(state, identifier)
        lifecycle = ReviewPreserved(removed) if isinstance(removed, Failure) else ReviewRemoved()
    return review_result(owned, lifecycle, as_json)


def review_result(owned: Session, lifecycle: ReviewLifecycle, as_json: bool) -> int:
    match lifecycle:
        case ReviewManaged():
            state = {"type": "REVIEW_MANAGED"}
        case ReviewRemoved():
            state = {"type": "REVIEW_REMOVED"}
        case ReviewPreserved(failure):
            state = {"type": "REVIEW_PRESERVED", "failure": failure_json(failure)}
    if as_json:
        print(json.dumps({"type": "WORK_REVIEW", "session": owned.identifier, "path": str(owned.path),
                          "head": owned.head, "url": owned.pr.url, "account": owned.pr.account.name,
                          "lifecycle": state}))
    elif isinstance(lifecycle, ReviewRemoved):
        print(f"Removed unchanged review session {owned.identifier}")
    else:
        print(f"session={owned.identifier} path={owned.path}")
    if isinstance(lifecycle, ReviewPreserved):
        return report(Failure(lifecycle.failure.stage, lifecycle.failure.reason, owned.identifier))
    return 0


def remove_review(state: Path, identifier: str) -> Session | Failure:
    try:
        canonical = str(uuid.UUID(identifier))
    except ValueError:
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    if canonical != identifier or safe_state(state) or safe_state(state / "reviews") or safe_state(state / "sessions"):
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    manifest = state / "sessions" / f"{identifier}.json"
    value = read_json(manifest, Stage.CLEANUP)
    owned = session_from(value, state, identifier)
    if isinstance(owned, Failure):
        return owned
    if owned.path.is_symlink() or not owned.path.is_dir() or (owned.path / ".git").is_symlink() or not (owned.path / ".git").is_file():
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    registered = worktrees(owned.repository, Stage.CLEANUP)
    observed_common = common_directory(owned.path, Stage.CLEANUP)
    observed_admin = git_directory(owned.path, Stage.CLEANUP)
    marker = read_json(owned.git_directory / "shell-config-work-session.json", Stage.CLEANUP)
    actual = next((entry for entry in registered if entry.path == owned.path), None) if isinstance(registered, tuple) else None
    if actual is None or not actual.detached or observed_common != owned.common or common_directory(owned.repository, Stage.CLEANUP) != owned.common or observed_admin != owned.git_directory or marker != {"type": "WORKTREE_OWNERSHIP", "session": identifier}:
        return Failure(Stage.CLEANUP, Reason.OWNERSHIP_UNPROVEN)
    if actual.head != owned.head:
        return Failure(Stage.CLEANUP, Reason.HEAD_CHANGED)
    indexed = git(owned.path, "ls-files", "-v", "-z", stage=Stage.CLEANUP)
    if isinstance(indexed, Failure):
        return indexed
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in indexed.stdout.split("\0")):
        return Failure(Stage.CLEANUP, Reason.WORKTREE_DIRTY)
    status = git(owned.path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", stage=Stage.CLEANUP)
    if isinstance(status, Failure):
        return status
    if status.stdout:
        return Failure(Stage.CLEANUP, Reason.WORKTREE_DIRTY)
    # Disable hooks and never use force. Git's own dirtiness check is a second
    # guard against ordinary edits between preflight and removal.
    removed = git(owned.repository, "-c", "core.hooksPath=/dev/null", "worktree", "remove", str(owned.path), stage=Stage.CLEANUP)
    if isinstance(removed, Failure):
        return removed
    after = worktrees(owned.repository, Stage.CLEANUP)
    if not isinstance(after, tuple) or owned.path.exists() or any(entry.path == owned.path for entry in after):
        return Failure(Stage.CLEANUP, Reason.REMOVE_UNVERIFIED)
    try:
        manifest.unlink()
    except OSError:
        return Failure(Stage.CLEANUP, Reason.IO_FAILED)
    return owned


def cleanup(state: Path, identifier: str) -> int:
    removed = remove_review(state, identifier)
    if isinstance(removed, Failure):
        return report(Failure(removed.stage, removed.reason, identifier))
    print(f"Removed review session {removed.identifier}")
    return 0


def accounts_command(config: Config, state: Path, selected: str | None, as_json: bool) -> int:
    identities = accounts_for(config)
    if isinstance(identities, Failure):
        print("Authenticate an account with gh auth login --hostname HOST; then run work accounts.", file=sys.stderr)
        return report(identities)
    if selected:
        choice = next((entry for entry in identities if entry.name == selected), None)
        if choice is None:
            return report(Failure(Stage.ACCOUNT, Reason.ACCOUNT_UNKNOWN))
        rejected = ensure_state(state) or write_private(state / "account.json", {"type": "WORK_ACCOUNT_SELECTION", "version": 1, "account": choice.name})
        if rejected:
            return report(rejected)
        print(f"Default review account: {choice.name} ({choice.hostname.value}/{choice.login})")
        return 0
    values = [{"type": "WORK_ACCOUNT", "name": entry.name, "hostname": entry.hostname.value, "login": entry.login} for entry in identities]
    if as_json:
        print(json.dumps({"type": "WORK_ACCOUNTS", "accounts": values}))
    else:
        for entry in identities:
            print(f"{entry.name:28} {entry.hostname.value}/{entry.login}")
        print("Select a default with work accounts NAME, or use review --account NAME.")
        print("Add another identity with gh auth login --hostname HOST. Global active accounts stay unchanged.")
    return 0


def new_ticket(config: Config, project: str | None, summary: str | None, kind: str, dry_run: bool) -> int:
    project = project or config.project
    interactive = not project or not summary
    if not project or not summary:
        if not sys.stdin.isatty():
            return report(Failure(Stage.CREATE, Reason.INPUT_REQUIRED))
        project = project or input("Jira project: ").strip()
        summary = summary or input("Summary: ").strip()
        kind = input(f"Type [{kind}]: ").strip() or kind
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", project) or not text(summary) or not text(kind, 100):
        return report(Failure(Stage.CREATE, Reason.INPUT_REQUIRED))
    args = ["acli", "jira", "workitem", "create", "--project", project, "--type", kind, "--summary", summary]
    if dry_run:
        print(shlex.join(args))
        return 0
    with tempfile.TemporaryDirectory(prefix="work-ticket-") as temporary:
        description = Path(temporary) / "description.md"
        description.write_text("")
        description.chmod(0o600)
        try:
            edited = subprocess.run([*config.editor, str(description)], env=clean_environment())
            body = description.read_text()
        except (OSError, UnicodeError):
            return report(Failure(Stage.CREATE, Reason.IO_FAILED))
        if edited.returncode:
            return report(Failure(Stage.CREATE, Reason.COMMAND_FAILED, config.editor[0]))
        if len(body) > 200_000 or any(ord(c) < 32 and c not in "\n\r\t" for c in body):
            return report(Failure(Stage.CREATE, Reason.INPUT_REQUIRED))
        if interactive:
            print(f"Create {kind} in {project}: {summary}\n\n{body[:4000]}")
            if input("Create ticket? [y/N]: ").strip().lower() != "y":
                return 0
        result = command([*args, "--description-file", str(description), "--json"], Stage.CREATE)
    if isinstance(result, Failure):
        # A request failure cannot prove that the server did not create a ticket.
        return report(Failure(Stage.CREATE, Reason.CREATE_UNVERIFIED))
    value = decoded(result.stdout, Stage.CREATE)
    created = created_ticket(value, project)
    if isinstance(created, Failure):
        return report(created)
    output = {"type": "JIRA_CREATED", "key": created.key, "id": created.identifier}
    if isinstance(created.link, TicketLink):
        output["url"] = created.link.url
    print(json.dumps(output))
    return 0


def created_ticket(value: object, project: str) -> CreatedTicket | Failure:
    if not isinstance(value, dict) or not isinstance(value.get("key"), str) or not re.fullmatch(re.escape(project) + r"-[1-9][0-9]*", value["key"]) or not isinstance(value.get("id"), str) or not re.fullmatch(r"[1-9][0-9]*", value["id"]):
        return Failure(Stage.CREATE, Reason.CREATE_UNVERIFIED)
    raw_url = value.get("self")
    if raw_url is None:
        return CreatedTicket(value["key"], value["id"], NoTicketLink())
    if not isinstance(raw_url, str):
        return Failure(Stage.CREATE, Reason.CREATE_UNVERIFIED)
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return Failure(Stage.CREATE, Reason.CREATE_UNVERIFIED)
    host = hostname(parsed.hostname)
    if isinstance(host, Failure) or parsed.scheme != "https" or parsed.netloc != host.value or parsed.query or parsed.fragment or parsed.path != f"/rest/api/3/issue/{value['id']}":
        return Failure(Stage.CREATE, Reason.CREATE_UNVERIFIED)
    return CreatedTicket(value["key"], value["id"], TicketLink(f"https://{host.value}/browse/{value['key']}"))


def check_summary(value: object) -> CheckState | Failure:
    if value is None or value == []:
        return CheckState.UNVERIFIED
    if not isinstance(value, list) or len(value) > 1000:
        return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    outcomes = []
    for entry in value:
        if not isinstance(entry, dict):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT)
        if entry.get("__typename") == "CheckRun":
            status = entry.get("status")
            if not isinstance(status, str) or status not in {"COMPLETED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}:
                return Failure(Stage.READ, Reason.INVALID_OUTPUT)
            conclusion = entry.get("conclusion")
            if conclusion is not None and not isinstance(conclusion, str):
                return Failure(Stage.READ, Reason.INVALID_OUTPUT)
            if status != "COMPLETED":
                if conclusion not in ("", None):
                    return Failure(Stage.READ, Reason.INVALID_OUTPUT)
                outcomes.append(CheckState.PENDING)
            elif conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                outcomes.append(CheckState.PASSING)
            elif conclusion in {"ACTION_REQUIRED", "TIMED_OUT", "CANCELLED", "FAILURE", "STARTUP_FAILURE"}:
                outcomes.append(CheckState.FAILING)
            elif conclusion == "STALE":
                outcomes.append(CheckState.UNVERIFIED)
            else:
                return Failure(Stage.READ, Reason.INVALID_OUTPUT)
        elif entry.get("__typename") == "StatusContext":
            state = entry.get("state")
            if not isinstance(state, str):
                return Failure(Stage.READ, Reason.INVALID_OUTPUT)
            if state == "SUCCESS":
                outcomes.append(CheckState.PASSING)
            elif state in {"ERROR", "FAILURE"}:
                outcomes.append(CheckState.FAILING)
            elif state in {"EXPECTED", "PENDING"}:
                outcomes.append(CheckState.PENDING)
            else:
                return Failure(Stage.READ, Reason.INVALID_OUTPUT)
        else:
            return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    for outcome in (CheckState.FAILING, CheckState.UNVERIFIED, CheckState.PENDING):
        if outcome in outcomes:
            return outcome
    return CheckState.PASSING


def pr_details(pr: PullRequest) -> PrDetails | Failure:
    scoped = verified_gh(pr.account)
    if isinstance(scoped, Failure):
        return scoped
    result = command(["gh", "pr", "view", str(pr.number), "--repo", pr.repository.gh_name,
                      "--json", "number,title,url,body,headRefOid,state,statusCheckRollup,reviewDecision"],
                     Stage.READ, env=scoped.environment())
    if isinstance(result, Failure):
        return result
    value = decoded(result.stdout, Stage.READ)
    if not isinstance(value, dict) or set(value) != {"number", "title", "url", "body", "headRefOid", "state", "statusCheckRollup", "reviewDecision"} or type(value["number"]) is not int or value["number"] != pr.number or value["url"] != pr.url or not isinstance(value["state"], str) or value["state"] not in {"OPEN", "CLOSED", "MERGED"} or not text(value["title"]) or not isinstance(value["body"], str) or not isinstance(value["headRefOid"], str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["headRefOid"]):
        return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    checks = check_summary(value["statusCheckRollup"])
    if isinstance(checks, Failure):
        return checks
    decision = value["reviewDecision"]
    if decision in ("", None):
        reviewed = ReviewState.UNVERIFIED
    else:
        try:
            reviewed = ReviewState(decision)
        except (ValueError, TypeError):
            return Failure(Stage.READ, Reason.INVALID_OUTPUT)
    body = "".join(c for c in value["body"][:8000] if ord(c) >= 32 or c in "\n\t")
    return PrDetails(pr, value["title"], body, value["headRefOid"], checks, reviewed)


def preview_item(value: object, config: Config) -> str | Failure:
    if not isinstance(value, dict):
        return Failure(Stage.PICKER, Reason.INVALID_OUTPUT)
    if value.get("type") == "JIRA_TICKET":
        if set(value) != {"type", "key", "title", "status"} or not text(value["key"], 100) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", value["key"]) or not text(value["title"]) or not text(value["status"], 100):
            return Failure(Stage.PICKER, Reason.INVALID_OUTPUT)
        return f"{value['title']}\n\n{value['key']}\nStatus: {value['status']}\n\no: open in Jira\nn: create a ticket"
    fields = {"type", "account", "hostname", "repository", "number", "title", "url", "queue", "draft", "checks", "review"}
    if value.get("type") != "PULL_REQUEST" or set(value) != fields or type(value["number"]) is not int or type(value["draft"]) is not bool or not text(value["account"], 100) or not text(value["title"]) or value["checks"] != "UNVERIFIED" or value["review"] != "UNVERIFIED" or not isinstance(value["queue"], str) or value["queue"] not in {entry.value for entry in Queue}:
        return Failure(Stage.PICKER, Reason.INVALID_OUTPUT)
    identities = accounts_for(config)
    if isinstance(identities, Failure):
        return identities
    selected = next((entry for entry in identities if entry.name == value["account"] and entry.hostname.value == value["hostname"]), None)
    parsed = parse_pr_url(value["url"]) if isinstance(value["url"], str) else Failure(Stage.PICKER, Reason.INVALID_OUTPUT)
    if selected is None or isinstance(parsed, Failure) or parsed[0].hostname != selected.hostname or parsed[0].full_name != value["repository"] or parsed[1] != value["number"]:
        return Failure(Stage.PICKER, Reason.INVALID_OUTPUT)
    details = pr_details(PullRequest(selected, parsed[0], parsed[1]))
    if isinstance(details, Failure):
        return details
    return (f"{details.title}\n\n{details.pr.repository.full_name} #{details.pr.number}\n"
            f"Account: {selected.name} @ {selected.hostname.value}\n"
            f"Checks: {details.checks.value}\nReview: {details.review.value}\n"
            f"Head: {details.head[:12]}\n{details.pr.url}\n\n{details.body}\n\n"
            "r: open a detached review worktree\no: open in browser")


def open_item(item: Item) -> int:
    if isinstance(item, JiraItem):
        result = command(["acli", "jira", "workitem", "view", item.key, "--web"], Stage.PICKER)
    else:
        scoped = verified_gh(item.pr.account)
        if isinstance(scoped, Failure):
            return report(scoped)
        result = command(["gh", "pr", "view", str(item.pr.number), "--repo", item.pr.repository.gh_name, "--web"],
                         Stage.PICKER, env=scoped.environment())
    return report(result) if isinstance(result, Failure) else 0


def panel(config: Config, state: Path, config_path: Path) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return report(Failure(Stage.PICKER, Reason.TTY_REQUIRED))
    if not shutil.which("fzf"):
        return report(Failure(Stage.PICKER, Reason.TOOL_UNAVAILABLE, "fzf"))
    while True:
        sources = collect(config)
        items = tuple(item for source in sources if isinstance(source, SourceSuccess) for item in source.items)
        failures = [f"{source.name}: {source.failure.reason.value}" for source in sources if isinstance(source, SourceFailure)]
        if failures:
            print("; ".join(failures), file=sys.stderr)
        header = " | ".join(f"{action.key}: {action.name}" for action in ACTIONS)
        if failures:
            header += "\nUnavailable: " + "; ".join(failures)
        rows = []
        for index, item in enumerate(items):
            if isinstance(item, PrItem):
                label = f"PR {item.pr.account.name} {item.pr.repository.full_name} #{item.pr.number} [{item.queue.value}] {item.title}"
            else:
                label = f"Jira {item.key} [{item.status}] {item.title}"
            rows.append(f"{index}\t{label}")
        with tempfile.TemporaryDirectory(prefix="work-preview-") as temporary:
            data = Path(temporary) / "items.json"
            data.write_text(json.dumps([item_json(item) for item in items]))
            data.chmod(0o600)
            preview = shlex.join([sys.executable, str(Path(__file__).resolve()), "_preview", str(data), str(config_path)]) + " {1}"
            result = subprocess.run(["fzf", "--delimiter", "\t", "--with-nth", "2..", "--prompt", "work> ",
                                     "--header", header, "--expect", ",".join(action.key for action in ACTIONS if action.key != "enter"),
                                     "--preview", preview, "--preview-window", "right:45%:wrap", "--no-multi"],
                                     input="\n".join(rows) + "\n", text=True, stdout=subprocess.PIPE, env=clean_environment())
        if result.returncode in (1, 130):
            return 0
        if result.returncode:
            return report(Failure(Stage.PICKER, Reason.COMMAND_FAILED, "fzf"))
        output = result.stdout.splitlines()
        key = output[0] if output else ""
        action = next((entry.name for entry in ACTIONS if entry.key == (key or "enter")), "quit")
        if action == "quit":
            return 0
        if action == "help":
            for entry in ACTIONS:
                print(f"{entry.key:8} {entry.description}")
            input("Press Enter to return to work.")
        elif action == "new":
            new_ticket(config, None, None, "Task", False)
        elif action == "refresh":
            continue
        elif len(output) > 1:
            selected = output[1].split("\t", 1)[0]
            if not selected.isdecimal() or int(selected) >= len(items):
                return report(Failure(Stage.PICKER, Reason.INVALID_OUTPUT))
            item = items[int(selected)]
            if action == "review":
                if isinstance(item, PrItem):
                    review(config, item.pr.url, item.pr.account.name, None, state)
                else:
                    print("Select a PR to start a review.", file=sys.stderr)
            else:
                open_item(item)


def default_config() -> Path:
    configured = os.environ.get("WORK_CONFIG") or os.environ.get("WORK_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser().absolute()
    installed = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "work/config.json"
    if installed.exists():
        return installed
    return Path(__file__).resolve().parents[1] / "config/work/config.json"


def main(argv: list[str]) -> int:
    if argv and argv[0] == "_preview":
        if len(argv) != 4 or not argv[3].isdecimal():
            return 1
        value = read_json(Path(argv[1]), Stage.PICKER)
        config = load_config(Path(argv[2]))
        if not isinstance(value, list) or int(argv[3]) >= len(value) or isinstance(config, Failure):
            return 1
        preview = preview_item(value[int(argv[3])], config)
        if isinstance(preview, Failure):
            print(f"Preview unavailable: stage={preview.stage.value} outcome={preview.reason.value}")
            return 1
        print(preview[:8000])
        return 0
    parser = argparse.ArgumentParser(prog="work", description=__doc__)
    parser.add_argument("--config", default=str(default_config()))
    parser.add_argument("--state-dir", default=os.environ.get("WORK_STATE_DIR", str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "shell-config/work")))
    commands = parser.add_subparsers(dest="command")
    listed = commands.add_parser("list", help="List current-user PRs and assigned Jira tickets")
    listed.add_argument("--json", action="store_true")
    identities = commands.add_parser("accounts", help="Discover accounts or select a default review account")
    identities.add_argument("name", nargs="?")
    identities.add_argument("--json", action="store_true")
    commands.add_parser("setup", help="Print authentication and local configuration guidance")
    created = commands.add_parser("new", help="Create a Jira ticket")
    created.add_argument("--project")
    created.add_argument("--summary")
    created.add_argument("--type", default="Task")
    created.add_argument("--dry-run", action="store_true")
    reviewed = commands.add_parser("review", help="Open a PR in an owned detached worktree")
    reviewed.add_argument("target")
    reviewed.add_argument("--account")
    reviewed.add_argument("--repo")
    reviewed.add_argument("--json", action="store_true")
    reviewed.add_argument("--no-editor", action="store_true")
    removed = commands.add_parser("cleanup", help="Remove an unchanged, clean owned review worktree")
    removed.add_argument("session")
    args = parser.parse_args(argv)
    config = load_config(Path(args.config).expanduser().absolute())
    if isinstance(config, Failure):
        return report(config)
    state = Path(args.state_dir).expanduser().absolute()
    if args.command == "setup":
        print("GitHub: gh auth login --hostname HOST (repeat for each account); work accounts")
        print("Jira Cloud: acli jira auth login --web")
        print(f"Public config: {args.config}\nLocal account/repository/project overlay: {Path(args.config).with_name('config.local.json')}")
        return 0
    if args.command == "accounts":
        return accounts_command(config, state, args.name, args.json)
    if args.command == "list":
        return list_work(config, args.json)
    if args.command == "new":
        return new_ticket(config, args.project, args.summary, args.type, args.dry_run)
    if args.command == "review":
        return review(config, args.target, args.account, args.repo, state, args.json, open_editor=not args.no_editor)
    if args.command == "cleanup":
        return cleanup(state, args.session)
    return panel(config, state, Path(args.config).expanduser().absolute())


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (KeyboardInterrupt, EOFError):
        raise SystemExit(130)

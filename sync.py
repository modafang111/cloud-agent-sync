#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D:\\dev 配下の複数 Git リポジトリを安全に一括同期する。

破壊的操作（force push / reset --hard / clean -fd / 履歴の強制書き換え）は行わない。
既存リポジトリの remote / branch / .git 設定は変更しない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import IO, Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = Path(r"D:\dev")
REPOS_FILE_NAME = "repositories.json"
CONFIG_FILE_NAME = "config.json"
EXAMPLE_REPOS_FILE_NAME = "repositories.example.json"

ALLOWED_GIT_VERBS = frozenset(
    {
        "rev-parse",
        "remote",
        "branch",
        "status",
        "fetch",
        "merge",
        "add",
        "diff",
        "commit",
        "push",
        "log",
        "rev-list",
        "show-ref",
        "symbolic-ref",
        "ls-files",
        "cat-file",
        "diff-index",
        "name-rev",
        "init",
    }
)

IN_PROGRESS_FILES = (
    "MERGE_HEAD",
    "REBASE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "REBASE_APPLY",
    "REBASE_MERGE",
)

NETWORK_ERROR_MARKERS = (
    "could not resolve host",
    "unable to access",
    "failed to connect",
    "connection refused",
    "connection timed out",
    "timed out",
    "ssl certificate problem",
    "authentication failed",
    "permission denied (publickey)",
    "could not read username",
    "the requested url returned error: 403",
    "the requested url returned error: 401",
    "fatal: could not read from remote repository",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "root_dir": r"D:\dev",
    "max_depth": 2,
    "auto_commit": True,
    "commit_message_format": "auto sync {timestamp}",
    "fetch_timeout_seconds": 120,
    "push_timeout_seconds": 120,
    "log_dir": "logs",
    "exclude_dir_names": [
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".cursor",
        "logs",
        "log",
        "tmp",
        "temp",
    ],
    "preferred_remote": "origin",
    "github_owner": "",
    "new_repo_private": True,
    "provision_non_git": True,
}


class ResultKind(str, Enum):
    OK = "OK"
    PULL = "PULL"
    PUSH = "PUSH"
    SKIP = "SKIP"
    CREATE = "CREATE"
    CONFLICT = "CONFLICT"
    ERROR = "ERROR"


class Decision(str, Enum):
    SKIP_IDENTICAL = "skip_identical"
    PULL_FF = "pull_ff"
    PUSH_LOCAL = "push_local"
    COMMIT_THEN_PUSH = "commit_then_push"
    CONFLICT_DIVERGED = "conflict_diverged"
    CONFLICT_DIRTY_AND_REMOTE = "conflict_dirty_and_remote"
    CONFLICT_UNMERGED = "conflict_unmerged"
    CONFLICT_IN_PROGRESS = "conflict_in_progress"
    ERROR_INVALID_REPO = "error_invalid_repo"
    ERROR_NO_REMOTE = "error_no_remote"
    ERROR_DETACHED = "error_detached"
    ERROR_NO_REMOTE_BRANCH = "error_no_remote_branch"
    ERROR_UNCOMMITTED = "error_uncommitted"
    ERROR_NO_HEAD = "error_no_head"
    ERROR_NETWORK = "error_network"


@dataclass
class RepoConfig:
    name: str
    path: str
    enabled: bool = True


@dataclass
class AppConfig:
    root_dir: Path
    max_depth: int
    auto_commit: bool
    commit_message_format: str
    fetch_timeout_seconds: int
    push_timeout_seconds: int
    log_dir: Path
    exclude_dir_names: set[str]
    preferred_remote: str
    github_owner: str
    new_repo_private: bool
    provision_non_git: bool


@dataclass
class RepoSnapshot:
    path: Path
    name: str
    valid: bool
    detached: bool = False
    branch: str = ""
    remotes: dict[str, str] = field(default_factory=dict)
    remote_name: str = ""
    remote_url: str = ""
    remote_branch_exists: bool = False
    dirty: bool = False
    porcelain: str = ""
    local_ahead: int = 0
    remote_ahead: int = 0
    in_progress: list[str] = field(default_factory=list)
    unmerged_files: list[str] = field(default_factory=list)
    head: str = ""
    remote_head: str = ""
    error: str = ""


@dataclass
class SetupResult:
    repos: list[RepoConfig]
    run_sync: bool


@dataclass
class RepoResult:
    kind: ResultKind
    name: str
    path: Path
    branch: str = ""
    message: str = ""
    details: list[str] = field(default_factory=list)
    conflict_files: list[str] = field(default_factory=list)
    git_state: str = ""
    network_error: bool = False
    identity_error: bool = False
    actions: list[str] = field(default_factory=list)


class UnsafeGitCommandError(RuntimeError):
    pass


class GitCommandError(RuntimeError):
    def __init__(self, message: str, stderr: str = "", returncode: int = 1) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def is_windows() -> bool:
    return os.name == "nt"


def now_stamp() -> datetime:
    return datetime.now().astimezone()


def format_commit_message(fmt: str, when: datetime | None = None) -> str:
    when = when or now_stamp()
    timestamp = when.strftime("%Y-%m-%d %H:%M:%S")
    return fmt.replace("{timestamp}", timestamp)


def configure_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


IDENTITY_MISSING_MESSAGE = "Git の user.name / user.email が未設定のため commit できません"
IDENTITY_HINT_LINES = (
    "未コミットの変更を自動 commit するには、一度だけ Git の作者名を設定してください。",
    "  git config --global user.name \"Your Name\"",
    "  git config --global user.email \"you@example.com\"",
    "既存リポジトリの remote / branch は変更しません。設定後にもう一度 sync を実行してください。",
)


def looks_like_identity_error(text: str) -> bool:
    lower = text.lower()
    return "author identity unknown" in lower or "please tell me who you are" in lower


def git_config_get(key: str, repo: Path | None = None) -> str:
    command = ["git"]
    if repo is not None:
        command.extend(["-C", str(repo)])
    command.extend(["config", "--get", key])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def read_git_identity(repo: Path | None = None) -> tuple[str, str]:
    return git_config_get("user.name", repo), git_config_get("user.email", repo)


def has_git_identity(repo: Path | None = None) -> bool:
    name, email = read_git_identity(repo)
    return bool(name and email)


def looks_like_network_error(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in NETWORK_ERROR_MARKERS)


def git_dir(path: Path) -> Path | None:
    marker = path / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        try:
            content = marker.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if content.lower().startswith("gitdir:"):
            raw = content.split(":", 1)[1].strip()
            resolved = (path / raw).resolve() if not os.path.isabs(raw) else Path(raw)
            return resolved if resolved.exists() else None
    return None


def is_git_repo(path: Path) -> bool:
    return git_dir(path) is not None


def _command_verb(args: Sequence[str]) -> str:
    i = 0
    while i < len(args):
        item = args[i]
        if item == "-c" and i + 1 < len(args):
            i += 2
            continue
        if item.startswith("-"):
            i += 1
            continue
        return item
    return ""


def assert_safe_git_args(args: Sequence[str]) -> None:
    verb = _command_verb(args)
    if verb not in ALLOWED_GIT_VERBS:
        raise UnsafeGitCommandError(f"許可されていない git コマンドです: {' '.join(args)}")

    joined = list(args)
    if verb == "config" and any(x in joined for x in ("--local", "--global", "--system", "--worktree")):
        # 読み取りのみ。--get / --get-regexp / --list 以外は禁止。
        if not any(x in joined for x in ("--get", "--get-all", "--get-regexp", "--list", "-l")):
            raise UnsafeGitCommandError("git config の書き込みは禁止です")
    if verb == "config" and any(
        x.startswith("--add") or x.startswith("--unset") or x == "--replace-all" for x in joined
    ):
        raise UnsafeGitCommandError("git config の書き込みは禁止です")

    if verb == "merge":
        if "--ff-only" not in joined:
            raise UnsafeGitCommandError("merge は --ff-only のみ許可します")
        if any(x in joined for x in ("--no-ff", "--squash", "--abort", "--continue")):
            raise UnsafeGitCommandError("許可されていない merge オプションです")

    if verb == "push":
        forbidden = {"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "--prune"}
        if forbidden.intersection(joined):
            raise UnsafeGitCommandError("破壊的な push オプションは禁止です")
        if "-u" in joined or "--set-upstream" in joined:
            raise UnsafeGitCommandError("upstream の変更は行いません")

    if verb == "fetch":
        if {"--force", "-f", "--update-head-ok"}.intersection(joined):
            raise UnsafeGitCommandError("破壊的な fetch オプションは禁止です")

    if verb in {"reset", "clean", "rebase", "checkout", "switch", "restore", "stash"}:
        raise UnsafeGitCommandError(f"{verb} は許可されていません")

    if verb == "init":
        if "--bare" in joined or any(x.startswith("--template") for x in joined):
            raise UnsafeGitCommandError("許可されていない init オプションです")
        return

    if verb == "remote":
        rest = list(args[1:])
        if rest in ([], ["-v"]):
            return
        if len(rest) == 3 and rest[0] == "add" and rest[1] == "origin":
            return
        raise UnsafeGitCommandError("remote の変更は add origin 以外禁止です")

    if verb == "add" and any(x.startswith("--force") or x == "-f" for x in joined):
        raise UnsafeGitCommandError("git add --force は禁止です")

    if verb == "commit" and any(x in joined for x in ("--amend", "--fixup", "--squash")):
        raise UnsafeGitCommandError("commit の書き換えは禁止です")


class GitClient:
    def __init__(self, repo: Path, logger: "SyncLogger | None" = None) -> None:
        self.repo = repo
        self.logger = logger
        self.executed: list[list[str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: int = 60,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert_safe_git_args(args)
        self.executed.append(list(args))
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GCM_INTERACTIVE", "Never")
        env.setdefault("LC_ALL", "C")
        if extra_env:
            env.update(extra_env)
        git_opts = ["-c", "core.quotepath=false"]
        if _command_verb(args) == "commit":
            git_opts.extend(["-c", "commit.gpgsign=false"])
        command = [
            "git",
            *git_opts,
            "-C",
            str(self.repo),
            *args,
        ]
        if self.logger:
            self.logger.action(self.repo.name, f"git {' '.join(args)}")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(f"git {' '.join(args)} がタイムアウトしました", str(exc), 124) from exc
        if check and completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip() or f"git {' '.join(args)} failed"
            raise GitCommandError(message, completed.stderr or "", completed.returncode)
        return completed

    def capture(self, args: Sequence[str], timeout: int = 60) -> str:
        return self.run(args, timeout=timeout).stdout.strip()

    def try_capture(self, args: Sequence[str], timeout: int = 60) -> str:
        completed = self.run(args, timeout=timeout, check=False)
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()


class SyncLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] = self.log_path.open("a", encoding="utf-8", newline="\n")
        self.line(f"=== sync start {now_stamp().isoformat(timespec='seconds')} ===")
        self.line(f"log={self.log_path}")

    def close(self) -> None:
        try:
            self.line(f"=== sync end {now_stamp().isoformat(timespec='seconds')} ===")
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "SyncLogger":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def line(self, text: str) -> None:
        stamp = now_stamp().strftime("%Y-%m-%d %H:%M:%S")
        self._fh.write(f"[{stamp}] {text}\n")
        self._fh.flush()

    def action(self, project: str, text: str) -> None:
        self.line(f"{project} ACTION {text}")

    def result(self, result: RepoResult) -> None:
        status = "SUCCESS" if result.kind in {ResultKind.OK, ResultKind.PULL, ResultKind.PUSH, ResultKind.SKIP, ResultKind.CREATE} else "FAILURE"
        self.line(
            f"{result.name} RESULT kind={result.kind.value} status={status} branch={result.branch} path={result.path}"
        )
        if result.message:
            self.line(f"{result.name} MESSAGE {result.message}")
        for detail in result.details:
            self.line(f"{result.name} DETAIL {detail}")
        if result.conflict_files:
            self.line(f"{result.name} CONFLICT_FILES {', '.join(result.conflict_files)}")
        if result.git_state:
            self.line(f"{result.name} GIT_STATE {result.git_state}")


def load_config(path: Path) -> AppConfig:
    raw = dict(DEFAULT_CONFIG)
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"設定ファイルが不正です: {path}")
        raw.update(loaded)
    log_dir = Path(str(raw["log_dir"]))
    if not log_dir.is_absolute():
        log_dir = path.parent / log_dir
    exclude = {str(name) for name in raw.get("exclude_dir_names", [])}
    return AppConfig(
        root_dir=Path(str(raw["root_dir"])),
        max_depth=int(raw.get("max_depth", 2)),
        auto_commit=bool(raw.get("auto_commit", True)),
        commit_message_format=str(raw.get("commit_message_format", "auto sync {timestamp}")),
        fetch_timeout_seconds=int(raw.get("fetch_timeout_seconds", 120)),
        push_timeout_seconds=int(raw.get("push_timeout_seconds", 120)),
        log_dir=log_dir,
        exclude_dir_names=exclude,
        preferred_remote=str(raw.get("preferred_remote", "origin")),
        github_owner=str(raw.get("github_owner", "")).strip(),
        new_repo_private=bool(raw.get("new_repo_private", True)),
        provision_non_git=bool(raw.get("provision_non_git", True)),
    )


def load_repositories(path: Path) -> list[RepoConfig]:
    if not path.is_file():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    items = loaded.get("repositories", loaded) if isinstance(loaded, dict) else loaded
    repos: list[RepoConfig] = []
    if not isinstance(items, list):
        raise ValueError(f"リポジトリ設定が不正です: {path}")
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or Path(str(item.get("path", ""))).name)
        path_raw = str(item.get("path", "")).strip()
        if not path_raw:
            continue
        repos.append(RepoConfig(name=name, path=path_raw, enabled=bool(item.get("enabled", True))))
    return repos


def save_repositories(path: Path, repos: Iterable[RepoConfig]) -> None:
    payload = {
        "version": 1,
        "updated_at": now_stamp().isoformat(timespec="seconds"),
        "repositories": [
            {"name": repo.name, "path": repo.path, "enabled": repo.enabled} for repo in repos
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_repos(root: Path, max_depth: int, exclude_names: set[str]) -> list[Path]:
    found: list[Path] = []
    if not root.exists() or not root.is_dir():
        return found

    def walk(current: Path, depth: int) -> None:
        if is_git_repo(current):
            found.append(current.resolve())
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in exclude_names or child.name.startswith("."):
                continue
            walk(child, depth + 1)

    if is_git_repo(root):
        found.append(root.resolve())
        return found
    walk(root, 0)
    return found


def parse_remotes(text: str) -> dict[str, str]:
    remotes: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], parts[1])
    return remotes


def choose_remote(remotes: dict[str, str], preferred: str) -> tuple[str, str]:
    if preferred in remotes:
        return preferred, remotes[preferred]
    if remotes:
        name = next(iter(remotes))
        return name, remotes[name]
    return "", ""


def inspect_in_progress(repo: Path) -> list[str]:
    gd = git_dir(repo)
    if gd is None:
        return []
    found: list[str] = []
    for name in IN_PROGRESS_FILES:
        if (gd / name).exists():
            found.append(name)
    return found


def snapshot_repo(
    path: Path,
    *,
    preferred_remote: str,
    fetch: bool,
    fetch_timeout: int,
    logger: SyncLogger | None = None,
) -> RepoSnapshot:
    name = path.name
    snap = RepoSnapshot(path=path, name=name, valid=is_git_repo(path))
    if not snap.valid:
        snap.error = "Git リポジトリとして認識できません"
        return snap
    git = GitClient(path, logger=logger)
    try:
        inside = git.try_capture(["rev-parse", "--is-inside-work-tree"])
        if inside != "true":
            snap.valid = False
            snap.error = "作業ツリーではありません"
            return snap
        snap.head = git.try_capture(["rev-parse", "HEAD"])
        if not snap.head:
            snap.valid = False
            snap.error = "コミットがまだ無いため同期を停止しました"
            return snap
        branch = git.try_capture(["branch", "--show-current"])
        abbrev = git.try_capture(["rev-parse", "--abbrev-ref", "HEAD"])
        if not branch or abbrev == "HEAD":
            snap.detached = True
            snap.branch = ""
        else:
            snap.branch = branch
        snap.remotes = parse_remotes(git.try_capture(["remote", "-v"]))
        snap.remote_name, snap.remote_url = choose_remote(snap.remotes, preferred_remote)
        snap.porcelain = git.try_capture(["status", "--porcelain"])
        snap.dirty = bool(snap.porcelain)
        snap.in_progress = inspect_in_progress(path)
        unmerged = git.try_capture(["diff", "--name-only", "--diff-filter=U"])
        snap.unmerged_files = [line for line in unmerged.splitlines() if line.strip()]
        if fetch and snap.remote_name:
            git.run(["fetch", snap.remote_name], timeout=fetch_timeout)
        if snap.branch and snap.remote_name:
            remote_ref = f"{snap.remote_name}/{snap.branch}"
            remote_head = git.try_capture(["rev-parse", "--verify", f"refs/remotes/{remote_ref}"])
            if remote_head:
                snap.remote_branch_exists = True
                snap.remote_head = remote_head
                counts = git.try_capture(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"])
                parts = counts.split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    snap.local_ahead = int(parts[0])
                    snap.remote_ahead = int(parts[1])
        return snap
    except GitCommandError as exc:
        snap.error = str(exc)
        return snap


def decide(snap: RepoSnapshot, auto_commit: bool) -> Decision:
    if not snap.valid:
        return Decision.ERROR_INVALID_REPO
    if snap.in_progress:
        return Decision.CONFLICT_IN_PROGRESS
    if snap.unmerged_files:
        return Decision.CONFLICT_UNMERGED
    if snap.detached:
        return Decision.ERROR_DETACHED
    if not snap.remote_name:
        return Decision.ERROR_NO_REMOTE
    if not snap.remote_branch_exists:
        return Decision.ERROR_NO_REMOTE_BRANCH
    if snap.local_ahead > 0 and snap.remote_ahead > 0:
        return Decision.CONFLICT_DIVERGED
    if snap.dirty and snap.remote_ahead > 0:
        return Decision.CONFLICT_DIRTY_AND_REMOTE
    if snap.dirty:
        return Decision.COMMIT_THEN_PUSH if auto_commit else Decision.ERROR_UNCOMMITTED
    if snap.remote_ahead > 0:
        return Decision.PULL_FF
    if snap.local_ahead > 0:
        return Decision.PUSH_LOCAL
    return Decision.SKIP_IDENTICAL


def status_text(snap: RepoSnapshot) -> str:
    lines = [
        f"valid={snap.valid}",
        f"branch={snap.branch or '(detached)'}",
        f"remote={snap.remote_name} {snap.remote_url}".strip(),
        f"dirty={snap.dirty}",
        f"local_ahead={snap.local_ahead}",
        f"remote_ahead={snap.remote_ahead}",
    ]
    if snap.in_progress:
        lines.append("in_progress=" + ",".join(snap.in_progress))
    if snap.unmerged_files:
        lines.append("unmerged=" + ",".join(snap.unmerged_files))
    if snap.error:
        lines.append("error=" + snap.error)
    return "; ".join(lines)


def commit_if_needed(git: GitClient, message: str) -> bool:
    git.run(["add", "-A"])
    staged = git.run(["diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return False
    git.run(["commit", "-m", message])
    return True


def sync_one(
    repo: RepoConfig,
    app: AppConfig,
    logger: SyncLogger,
    *,
    dry_run: bool = False,
) -> RepoResult:
    path = Path(repo.path)
    name = repo.name or path.name
    result = RepoResult(kind=ResultKind.ERROR, name=name, path=path)

    if not path.exists():
        result.message = "パスが存在しません"
        result.details.append(str(path))
        return result

    try:
        snap = snapshot_repo(
            path,
            preferred_remote=app.preferred_remote,
            fetch=not dry_run,
            fetch_timeout=app.fetch_timeout_seconds,
            logger=logger,
        )
    except GitCommandError as exc:
        result.message = str(exc)
        result.network_error = looks_like_network_error(str(exc) + exc.stderr)
        result.git_state = "fetch failed"
        return result

    result.branch = snap.branch
    result.git_state = status_text(snap)

    if snap.error:
        result.message = snap.error
        result.network_error = looks_like_network_error(snap.error)
        if result.network_error:
            result.message = "GitHub接続エラー"
            result.details.append(snap.error)
        return result

    decision = decide(snap, app.auto_commit)
    git = GitClient(path, logger=logger)

    def conflict(kind_message: str, files: list[str] | None = None) -> RepoResult:
        result.kind = ResultKind.CONFLICT
        result.message = kind_message
        result.conflict_files = files or snap.unmerged_files
        result.details.extend(
            [
                f"プロジェクト: {name}",
                f"パス: {path}",
                f"現在の branch: {snap.branch or '(detached HEAD)'}",
                f"Git の状態: {result.git_state}",
            ]
        )
        if result.conflict_files:
            result.details.append("競合ファイル: " + ", ".join(result.conflict_files))
        elif snap.dirty:
            dirty_files = [line[3:] for line in snap.porcelain.splitlines() if line.strip()]
            if dirty_files:
                result.details.append("未コミットの変更: " + ", ".join(dirty_files[:20]))
        return result

    if decision == Decision.ERROR_INVALID_REPO:
        result.message = snap.error or "Git リポジトリとして正常ではありません"
        return result
    if decision == Decision.ERROR_DETACHED:
        result.message = "detached HEAD のため同期を停止しました"
        result.git_state = status_text(snap)
        return result
    if decision == Decision.ERROR_NO_REMOTE:
        result.message = "remote がありません。sync --provision で GitHub リポジトリを作成できます"
        return result
    if decision == Decision.ERROR_NO_REMOTE_BRANCH:
        result.message = (
            f"リモートに branch '{snap.branch}' が無いため同期を停止しました"
            "（既存の remote / branch は変更しません）"
        )
        return result
    if decision == Decision.ERROR_UNCOMMITTED:
        result.message = "未コミットの変更があります（auto_commit=false のため停止）"
        return result
    if decision == Decision.ERROR_NO_HEAD:
        result.message = "コミットがまだ無いため同期を停止しました"
        return result
    if decision == Decision.CONFLICT_IN_PROGRESS:
        return conflict(
            "rebase / merge / cherry-pick などが進行中です。自動同期できません。",
            snap.unmerged_files,
        )
    if decision == Decision.CONFLICT_UNMERGED:
        return conflict("未解決のコンフリクトがあります。", snap.unmerged_files)
    if decision == Decision.CONFLICT_DIVERGED:
        return conflict(
            "ローカルとリモートの双方に進んだコミットがあります。上書きせず停止しました。"
        )
    if decision == Decision.CONFLICT_DIRTY_AND_REMOTE:
        return conflict(
            "ローカルに未コミット変更があり、リモートにも新しいコミットがあります。自動同期できません。"
        )

    if dry_run:
        result.kind = {
            Decision.SKIP_IDENTICAL: ResultKind.SKIP,
            Decision.PULL_FF: ResultKind.PULL,
            Decision.PUSH_LOCAL: ResultKind.PUSH,
            Decision.COMMIT_THEN_PUSH: ResultKind.PUSH,
        }.get(decision, ResultKind.OK)
        result.message = f"dry-run: {decision.value}"
        return result

    try:
        if decision == Decision.SKIP_IDENTICAL:
            result.kind = ResultKind.SKIP
            result.message = "変更なし"
            return result

        if decision == Decision.PULL_FF:
            remote_ref = f"{snap.remote_name}/{snap.branch}"
            git.run(["merge", "--ff-only", remote_ref], timeout=app.fetch_timeout_seconds)
            result.kind = ResultKind.PULL
            result.message = "リモート変更を取得"
            result.actions.append("merge --ff-only")
            return result

        committed = False
        if decision == Decision.COMMIT_THEN_PUSH:
            if not app.auto_commit:
                result.message = "未コミットの変更があります（auto_commit=false）"
                result.kind = ResultKind.ERROR
                return result
            if not has_git_identity(path):
                result.kind = ResultKind.ERROR
                result.identity_error = True
                result.message = IDENTITY_MISSING_MESSAGE
                return result
            message = format_commit_message(app.commit_message_format)
            committed = commit_if_needed(git, message)
            if committed:
                result.actions.append(f"commit {message}")
            else:
                # add したがコミット対象が無い（改行差など）
                after = snapshot_repo(
                    path,
                    preferred_remote=app.preferred_remote,
                    fetch=False,
                    fetch_timeout=app.fetch_timeout_seconds,
                    logger=logger,
                )
                if after.local_ahead == 0 and after.remote_ahead == 0 and not after.dirty:
                    result.kind = ResultKind.SKIP
                    result.message = "変更なし"
                    return result

        if decision in {Decision.PUSH_LOCAL, Decision.COMMIT_THEN_PUSH}:
            push_spec = f"HEAD:refs/heads/{snap.branch}"
            git.run(
                ["push", snap.remote_name, push_spec],
                timeout=app.push_timeout_seconds,
            )
            result.actions.append(f"push {snap.remote_name} {push_spec}")
            result.kind = ResultKind.PUSH
            result.message = "ローカル変更を送信" if committed or decision == Decision.PUSH_LOCAL else "ローカル変更を送信"
            return result

        result.message = f"未対応の判定です: {decision.value}"
        return result
    except GitCommandError as exc:
        text = f"{exc}\n{exc.stderr}".strip()
        if looks_like_network_error(text):
            result.kind = ResultKind.ERROR
            result.network_error = True
            result.message = "GitHub接続エラー"
            result.details.append(str(exc))
            return result
        if looks_like_identity_error(text):
            result.kind = ResultKind.ERROR
            result.identity_error = True
            result.message = IDENTITY_MISSING_MESSAGE
            return result
        lower = text.lower()
        if "non-fast-forward" in lower or "failed to push some refs" in lower or "rejected" in lower:
            return conflict("push が rejected されました。双方に変更がある可能性があります。")
        if "not possible to fast-forward" in lower or "would be overwritten" in lower:
            return conflict("fast-forward できないため停止しました。コンフリクトの可能性があります。")
        result.kind = ResultKind.ERROR
        result.message = str(exc)
        return result


def print_banner() -> None:
    print("cloud-agent-sync  安全な一括 Git 同期")
    print("force push / reset --hard / clean は実行しません。")
    print()


def format_result_line(result: RepoResult) -> str:
    return f"[{result.kind.value}] {result.name} {result.message}"


def print_result(result: RepoResult) -> None:
    print(format_result_line(result))
    if result.kind == ResultKind.CONFLICT:
        for line in result.details:
            print(f"        {line}")
    elif result.kind == ResultKind.ERROR and result.details:
        for line in result.details:
            print(f"        {line}")


def print_summary(results: Sequence[RepoResult]) -> None:
    success = sum(1 for r in results if r.kind in {ResultKind.OK, ResultKind.PULL, ResultKind.PUSH, ResultKind.CREATE})
    skip = sum(1 for r in results if r.kind == ResultKind.SKIP)
    conflict = sum(1 for r in results if r.kind == ResultKind.CONFLICT)
    network = sum(1 for r in results if r.kind == ResultKind.ERROR and r.network_error)
    other = sum(1 for r in results if r.kind == ResultKind.ERROR and not r.network_error)
    print()
    print("----- 集計 -----")
    print(f"同期成功：{success}件")
    print(f"変更なし：{skip}件")
    print(f"コンフリクト：{conflict}件")
    print(f"接続エラー：{network}件")
    print(f"その他エラー：{other}件")
    if any(r.identity_error for r in results):
        print()
        for line in IDENTITY_HINT_LINES:
            print(line)


def stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def print_discovery_table(snaps: Sequence[RepoSnapshot]) -> None:
    print("検出された Git リポジトリ:")
    print()
    width = max((len(s.name) for s in snaps), default=8)
    for index, snap in enumerate(snaps, start=1):
        branch = snap.branch or ("DETACHED" if snap.detached else "-")
        remote = snap.remote_url or "(remote なし)"
        mark = "OK" if snap.valid and snap.remote_name and not snap.detached else "注意"
        print(f"  {index:>3}. {snap.name:<{width}}  {snap.path}")
        print(f"       branch={branch}  remote={remote}  [{mark}]")
    print()


def parse_selection(text: str, count: int) -> list[int] | None:
    raw = text.strip().lower()
    if raw in {"all", "a", "*"}:
        return list(range(1, count + 1))
    if raw in {"q", "quit", "n", "none"}:
        return []
    indexes: list[int] = []
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            if not start_s.isdigit() or not end_s.isdigit():
                return None
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            indexes.extend(range(start, end + 1))
            continue
        if not part.isdigit():
            return None
        indexes.append(int(part))
    if any(i < 1 or i > count for i in indexes):
        return None
    # 順序を保った unique
    seen: set[int] = set()
    unique: list[int] = []
    for i in indexes:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return unique


def interactive_select(snaps: Sequence[RepoSnapshot]) -> list[RepoConfig]:
    print("同期対象の番号を入力してください。")
    print("例: 1,2,5   3-6   all   （何も選ばない場合は q）")
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            return [
                RepoConfig(name=snap.name, path=str(snap.path), enabled=False) for snap in snaps
            ]
        parsed = parse_selection(answer, len(snaps))
        if parsed is None:
            print("入力を解釈できません。番号、範囲、all、q のいずれかを指定してください。")
            continue
        selected = [snaps[i - 1] for i in parsed]
        break
    enabled_paths = {s.path.resolve() for s in selected}
    repos: list[RepoConfig] = []
    for snap in snaps:
        repos.append(
            RepoConfig(
                name=snap.name,
                path=str(snap.path),
                enabled=snap.path.resolve() in enabled_paths,
            )
        )
    return repos


def scan_snapshots(app: AppConfig, logger: SyncLogger | None = None) -> list[RepoSnapshot]:
    paths = discover_repos(app.root_dir, app.max_depth, app.exclude_dir_names)
    snaps: list[RepoSnapshot] = []
    for path in paths:
        snap = snapshot_repo(
            path,
            preferred_remote=app.preferred_remote,
            fetch=False,
            fetch_timeout=app.fetch_timeout_seconds,
            logger=logger,
        )
        snaps.append(snap)
    return snaps


def cmd_doctor(app: AppConfig, repos_path: Path) -> int:
    print_banner()
    print("[環境チェック]")
    git_ok = False
    try:
        git_ver = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if git_ver.returncode == 0:
            print(f"  Git: {git_ver.stdout.strip()}")
            git_ok = True
        else:
            print("  Git: 見つかりません")
    except FileNotFoundError:
        print("  Git: インストールされていません")

    print(f"  Python: {sys.version.split()[0]}  ({sys.executable})")
    print(f"  管理ディレクトリ: {SCRIPT_DIR}")
    print(f"  同期ルート: {app.root_dir}  exists={app.root_dir.exists()}")
    print(f"  設定: {SCRIPT_DIR / CONFIG_FILE_NAME}  exists={(SCRIPT_DIR / CONFIG_FILE_NAME).is_file()}")
    print(f"  同期対象: {repos_path}  exists={repos_path.is_file()}")

    gh_ver = subprocess.run(
        ["gh", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if gh_ver.returncode == 0:
        print(f"  GitHub CLI: {gh_ver.stdout.splitlines()[0]}")
    else:
        print("  GitHub CLI: なし（必須ではありません。既存の Git 認証を使います）")

    if git_ok:
        user = subprocess.run(
            ["git", "config", "--global", "--get", "user.name"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        email = subprocess.run(
            ["git", "config", "--global", "--get", "user.email"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        name = user.stdout.strip() or "(未設定)"
        mail = email.stdout.strip() or "(未設定)"
        print(f"  git user.name: {name}")
        print(f"  git user.email: {mail}")
        print("  GitHub 認証: 既存の Git Credential Manager / SSH 設定をそのまま利用します")
        if name == "(未設定)" or mail == "(未設定)":
            print("  [警告] 自動 commit するには user.name と user.email が必要です。")
            print('         git config --global user.name "Your Name"')
            print('         git config --global user.email "you@example.com"')
        print("  GitHub リポジトリ未作成のプロジェクトは sync --provision で作成できます")

    if is_windows():
        user_path = os.environ.get("PATH", "")
        on_path = str(SCRIPT_DIR / "bin").rstrip("\\/") in user_path.replace("/", "\\") or str(
            SCRIPT_DIR
        ).rstrip("\\/") in user_path.replace("/", "\\")
        print(f"  PATH に sync コマンドあり: {on_path}")
    print()
    if not git_ok:
        print("Git をインストールしてから再実行してください。")
        return 1
    if not app.root_dir.exists():
        print(f"{app.root_dir} が見つかりません。config.json の root_dir を確認してください。")
        return 1
    print("doctor 完了。日常操作は sync だけです。")
    return 0


def ensure_repos_configured(
    app: AppConfig,
    repos_path: Path,
    logger: SyncLogger,
    *,
    force_init: bool = False,
) -> SetupResult | None:
    existing = load_repositories(repos_path) if repos_path.is_file() and not force_init else []
    if existing and not force_init:
        return SetupResult(repos=existing, run_sync=True)

    print_banner()
    if not app.root_dir.exists():
        print(f"同期ルート {app.root_dir} が存在しません。")
        print("Windows の D:\\dev を想定しています。Cloud Agent / 別環境では config.json を編集してください。")
        return None

    print(f"{app.root_dir} を走査して Git リポジトリを検出します...\n")
    snaps = scan_snapshots(app, logger)
    if not snaps:
        print("Git リポジトリが見つかりませんでした。")
        save_repositories(repos_path, [])
        print(f"空の設定を書きました: {repos_path}")
        return SetupResult(repos=[], run_sync=False)

    print_discovery_table(snaps)

    if stdin_is_tty():
        repos = interactive_select(snaps)
        save_repositories(repos_path, repos)
        enabled = [r for r in repos if r.enabled]
        print(f"\n設定を保存しました: {repos_path}")
        print(f"同期対象: {len(enabled)}件 / 検出 {len(repos)}件")
        if not enabled:
            print("有効なリポジトリがありません。repositories.json の enabled を true にしてください。")
            return SetupResult(repos=repos, run_sync=False)
        try:
            answer = input("今すぐ同期しますか? [Y/n] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer in {"", "y", "yes"}:
            return SetupResult(repos=repos, run_sync=True)
        print("設定のみ保存しました。次回から sync で同期できます。")
        return SetupResult(repos=repos, run_sync=False)

    repos = [
        RepoConfig(name=snap.name, path=str(snap.path), enabled=False) for snap in snaps
    ]
    save_repositories(repos_path, repos)
    print("対話入力が使えないため、検出結果をすべて enabled=false で保存しました。")
    print(f"設定ファイル: {repos_path}")
    print("同期したいプロジェクトの enabled を true にしてから、もう一度 sync を実行してください。")
    print("ターミナルから `sync --init` でも選択できます。")
    return SetupResult(repos=repos, run_sync=False)


def add_repo(repos_path: Path, target: Path, app: AppConfig) -> int:
    target = target.resolve()
    if not is_git_repo(target):
        eprint(f"Git リポジトリではありません: {target}")
        return 1
    repos = load_repositories(repos_path)
    for repo in repos:
        if Path(repo.path).resolve() == target:
            repo.enabled = True
            save_repositories(repos_path, repos)
            print(f"既に登録済みです。enabled=true にしました: {repo.name}")
            return 0
    snap = snapshot_repo(
        target,
        preferred_remote=app.preferred_remote,
        fetch=False,
        fetch_timeout=app.fetch_timeout_seconds,
    )
    repos.append(RepoConfig(name=snap.name, path=str(target), enabled=True))
    save_repositories(repos_path, repos)
    print(f"追加しました: {snap.name}  {target}")
    return 0


def remove_repo(repos_path: Path, name_or_path: str) -> int:
    repos = load_repositories(repos_path)
    if not repos:
        eprint("repositories.json がありません。先に sync --init を実行してください。")
        return 1
    needle = name_or_path.strip()
    remaining: list[RepoConfig] = []
    removed: list[RepoConfig] = []
    for repo in repos:
        if repo.name == needle or Path(repo.path).as_posix().rstrip("/") == Path(needle).as_posix().rstrip("/"):
            removed.append(repo)
        else:
            remaining.append(repo)
    if not removed:
        eprint(f"見つかりません: {needle}")
        return 1
    save_repositories(repos_path, remaining)
    for repo in removed:
        print(f"除外しました: {repo.name}  {repo.path}")
    return 0


def set_enabled(repos_path: Path, name: str, enabled: bool) -> int:
    repos = load_repositories(repos_path)
    matched = False
    for repo in repos:
        if repo.name == name or Path(repo.path).name == name:
            repo.enabled = enabled
            matched = True
    if not matched:
        eprint(f"見つかりません: {name}")
        return 1
    save_repositories(repos_path, repos)
    print(f"{'有効化' if enabled else '無効化'}しました: {name}")
    return 0


@dataclass
class GithubRepoInfo:
    exists: bool
    empty: bool = True
    url: str = ""


class GithubApi:
    def current_login(self) -> str:
        raise NotImplementedError

    def inspect(self, owner: str, name: str) -> GithubRepoInfo:
        raise NotImplementedError

    def create(self, owner: str, name: str, private: bool) -> str:
        raise NotImplementedError


class GhCliGithubApi(GithubApi):
    def _run(self, args: Sequence[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("GH_PROMPT_DISABLED", "1")
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        return completed

    def current_login(self) -> str:
        completed = self._run(["api", "user", "--jq", ".login"])
        if completed.returncode != 0:
            raise GitCommandError(
                (completed.stderr or completed.stdout or "gh api user に失敗しました").strip(),
                completed.stderr or "",
                completed.returncode,
            )
        return completed.stdout.strip()

    def inspect(self, owner: str, name: str) -> GithubRepoInfo:
        completed = self._run(["repo", "view", f"{owner}/{name}", "--json", "url,isEmpty"])
        if completed.returncode != 0:
            return GithubRepoInfo(exists=False)
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return GithubRepoInfo(exists=True, empty=False)
        return GithubRepoInfo(
            exists=True,
            empty=bool(payload.get("isEmpty", False)),
            url=str(payload.get("url") or f"https://github.com/{owner}/{name}.git"),
        )

    def create(self, owner: str, name: str, private: bool) -> str:
        visibility = "--private" if private else "--public"
        completed = self._run(["repo", "create", f"{owner}/{name}", visibility], timeout=180)
        if completed.returncode != 0:
            raise GitCommandError(
                (completed.stderr or completed.stdout or "gh repo create に失敗しました").strip(),
                completed.stderr or "",
                completed.returncode,
            )
        info = self.inspect(owner, name)
        return info.url or f"https://github.com/{owner}/{name}.git"


@dataclass
class ProvisionCandidate:
    path: Path
    name: str
    kind: str
    github_name: str
    note: str = ""


def github_repo_name(folder: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", folder.strip())
    name = name.strip(".-")
    return name or "project"


def looks_like_virtualenv(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(".venv") or name in {".venv", "venv", "env"}:
        return True
    return (path / "pyvenv.cfg").is_file()


def should_skip_non_git_dir(path: Path, exclude_names: set[str]) -> bool:
    name = path.name
    if name in exclude_names or name.startswith("."):
        return True
    if looks_like_virtualenv(path):
        return True
    return False


def discover_non_git_projects(root: Path, exclude_names: set[str]) -> list[Path]:
    found: list[Path] = []
    if not root.exists() or not root.is_dir():
        return found
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found
    for child in children:
        if not child.is_dir() or child.is_symlink():
            continue
        if should_skip_non_git_dir(child, exclude_names):
            continue
        if is_git_repo(child):
            continue
        found.append(child.resolve())
    return found


def collect_provision_candidates(app: AppConfig, logger: SyncLogger | None = None) -> list[ProvisionCandidate]:
    candidates: list[ProvisionCandidate] = []
    for path in discover_repos(app.root_dir, app.max_depth, app.exclude_dir_names):
        snap = snapshot_repo(
            path,
            preferred_remote=app.preferred_remote,
            fetch=False,
            fetch_timeout=app.fetch_timeout_seconds,
            logger=logger,
        )
        if snap.valid and not snap.remote_name:
            candidates.append(
                ProvisionCandidate(
                    path=path,
                    name=path.name,
                    kind="no_remote",
                    github_name=github_repo_name(path.name),
                    note="ローカル Git あり / GitHub remote なし",
                )
            )
    if app.provision_non_git:
        for path in discover_non_git_projects(app.root_dir, app.exclude_dir_names):
            candidates.append(
                ProvisionCandidate(
                    path=path,
                    name=path.name,
                    kind="not_git",
                    github_name=github_repo_name(path.name),
                    note="Git 未初期化",
                )
            )
    return candidates


def upsert_repository(repos: list[RepoConfig], path: Path, enabled: bool = True) -> list[RepoConfig]:
    resolved = path.resolve()
    for repo in repos:
        if Path(repo.path).resolve() == resolved:
            repo.enabled = enabled
            repo.name = repo.name or path.name
            return repos
    repos.append(RepoConfig(name=path.name, path=str(resolved), enabled=enabled))
    return repos


def provision_one(
    candidate: ProvisionCandidate,
    app: AppConfig,
    api: GithubApi,
    owner: str,
    logger: SyncLogger,
    *,
    dry_run: bool,
) -> RepoResult:
    path = candidate.path
    result = RepoResult(kind=ResultKind.ERROR, name=candidate.name, path=path)
    github_name = candidate.github_name
    clone_url = f"https://github.com/{owner}/{github_name}.git"

    if dry_run:
        result.kind = ResultKind.CREATE
        result.message = f"dry-run: {candidate.kind} → {owner}/{github_name}"
        return result

    try:
        if candidate.kind == "not_git":
            if is_git_repo(path):
                result.message = "すでに Git リポジトリです。remote だけ作成します"
            else:
                git = GitClient(path, logger=logger)
                git.run(["init", "-b", "main"])

        if not is_git_repo(path):
            result.message = "git init に失敗しました"
            return result

        git = GitClient(path, logger=logger)
        remotes = parse_remotes(git.try_capture(["remote", "-v"]))
        if remotes:
            result.message = "すでに remote があるため新規作成しません（既存 remote は変更しません）"
            return result

        head = git.try_capture(["rev-parse", "HEAD"])
        dirty = bool(git.try_capture(["status", "--porcelain"]))
        if dirty:
            if not has_git_identity(path):
                result.identity_error = True
                result.message = IDENTITY_MISSING_MESSAGE
                return result
            message = "initial commit" if not head else format_commit_message(app.commit_message_format)
            commit_if_needed(git, message)
            head = git.try_capture(["rev-parse", "HEAD"])

        info = api.inspect(owner, github_name)
        created = False
        if info.exists and not info.empty:
            result.message = (
                f"GitHub に {owner}/{github_name} が既にあり、空ではありません。"
                "履歴が衝突する可能性があるため接続しません"
            )
            return result
        if not info.exists:
            clone_url = api.create(owner, github_name, app.new_repo_private) or clone_url
            if not clone_url.endswith(".git"):
                clone_url = clone_url.rstrip("/") + ".git"
            created = True
        elif info.url:
            clone_url = info.url if info.url.endswith(".git") else info.url.rstrip("/") + ".git"

        git.run(["remote", "add", "origin", clone_url])
        head = git.try_capture(["rev-parse", "HEAD"])
        branch = git.try_capture(["branch", "--show-current"]) or "main"
        if head:
            git.run(
                ["push", "origin", f"HEAD:refs/heads/{branch}"],
                timeout=app.push_timeout_seconds,
            )
            result.actions.append(f"push origin HEAD:refs/heads/{branch}")
        result.kind = ResultKind.CREATE
        result.branch = branch
        if created:
            result.message = f"GitHub リポジトリ {owner}/{github_name} を作成し、同期対象に追加"
        else:
            result.message = f"空の GitHub リポジトリ {owner}/{github_name} に接続し、同期対象に追加"
        result.details.append(clone_url)
        return result
    except (GitCommandError, UnsafeGitCommandError, OSError) as exc:
        text = str(exc)
        result.message = text.splitlines()[0] if text else "新規作成に失敗しました"
        result.network_error = looks_like_network_error(text)
        result.identity_error = looks_like_identity_error(text)
        result.details.append(text)
        return result


def gh_available() -> bool:
    try:
        completed = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return completed.returncode == 0
    except FileNotFoundError:
        return False


def cmd_provision(
    app: AppConfig,
    repos_path: Path,
    logger: SyncLogger,
    *,
    yes: bool,
    dry_run: bool,
    api: GithubApi | None = None,
) -> int:
    print_banner()
    print("GitHub リポジトリが無いプロジェクトを新規作成し、同期対象に追加します。")
    print("すでに remote があるプロジェクトは変更しません。")
    print()

    if not gh_available() and api is None:
        print("GitHub CLI (gh) が見つかりません。gh がログイン済みである必要があります。")
        return 1
    if not app.root_dir.exists():
        print(f"{app.root_dir} が存在しません。")
        return 1

    host = api or GhCliGithubApi()
    try:
        owner = app.github_owner or host.current_login()
    except GitCommandError as exc:
        print("GitHub のユーザー名を取得できません。gh auth login を確認してください。")
        print(str(exc))
        return 1

    visibility = "private" if app.new_repo_private else "public"
    print(f"GitHub アカウント: {owner}")
    print(f"新規リポジトリの公開範囲: {visibility}")
    print()

    candidates = collect_provision_candidates(app, logger)
    if not candidates:
        print("新規作成が必要なプロジェクトはありません。")
        return 0

    print("作成候補:")
    for index, item in enumerate(candidates, start=1):
        print(f"  {index:>3}. {item.name:<28}  {item.path}")
        print(f"       {item.note}  →  https://github.com/{owner}/{item.github_name}")
    print()
    print("venv / logs などの作業フォルダは除外済みです。")
    print("Git 未初期化の行は、本当に開発プロジェクトのものだけ選んでください。")
    print("例: 1,2   1-3   all   （やめる場合は q）")

    if yes:
        selected = [item for item in candidates if item.kind == "no_remote"]
        if not selected:
            print("--yes では「Git あり / remote なし」だけを自動作成します。該当がありません。")
            return 0
        print("確認省略: remote が無い Git プロジェクトだけ作成します。")
    elif stdin_is_tty():
        while True:
            try:
                answer = input("> ").strip()
            except EOFError:
                print("中止しました。")
                return 0
            parsed = parse_selection(answer, len(candidates))
            if parsed is None:
                print("入力を解釈できません。番号、範囲、all、q のいずれかを指定してください。")
                continue
            selected = [candidates[i - 1] for i in parsed]
            break
        if not selected:
            print("中止しました。")
            return 0
    else:
        print("対話入力ができないため中止しました。ターミナルで sync --provision を実行してください。")
        return 0

    results: list[RepoResult] = []
    repos = load_repositories(repos_path)
    for item in selected:
        print(f"---- {item.name} ----")
        result = provision_one(item, app, host, owner, logger, dry_run=dry_run)
        print_result(result)
        logger.result(result)
        results.append(result)
        if result.kind == ResultKind.CREATE and not dry_run:
            repos = upsert_repository(repos, item.path, enabled=True)
        print()

    if not dry_run:
        save_repositories(repos_path, repos)
        print(f"設定を更新しました: {repos_path}")

    print_summary(results)
    created = sum(1 for r in results if r.kind == ResultKind.CREATE)
    print(f"新規作成：{created}件")
    print()
    print("以後は通常どおり sync だけ実行してください。")
    if any(r.kind in {ResultKind.CONFLICT, ResultKind.ERROR} for r in results):
        return 2
    return 0


def run_sync(
    app: AppConfig,
    repos: Sequence[RepoConfig],
    logger: SyncLogger,
    *,
    dry_run: bool,
) -> int:
    enabled = [repo for repo in repos if repo.enabled]
    disabled = [repo for repo in repos if not repo.enabled]
    print_banner()
    print(f"対象: {len(enabled)}件（無効 {len(disabled)}件はスキップ）")
    if dry_run:
        print("dry-run: commit / merge / push は行いません。")
    print()
    if not enabled:
        print("同期対象がありません。sync --init または repositories.json を編集してください。")
        return 0

    results: list[RepoResult] = []
    for repo in enabled:
        logger.line(f"BEGIN {repo.name} {repo.path}")
        print(f"---- {repo.name} ----")
        print(f"path: {repo.path}")
        result = sync_one(repo, app, logger, dry_run=dry_run)
        print_result(result)
        logger.result(result)
        results.append(result)
        print()

    print_summary(results)
    logger.line(
        "SUMMARY "
        + ", ".join(
            [
                f"ok={sum(1 for r in results if r.kind in {ResultKind.OK, ResultKind.PULL, ResultKind.PUSH, ResultKind.CREATE})}",
                f"skip={sum(1 for r in results if r.kind == ResultKind.SKIP)}",
                f"conflict={sum(1 for r in results if r.kind == ResultKind.CONFLICT)}",
                f"network={sum(1 for r in results if r.kind == ResultKind.ERROR and r.network_error)}",
                f"error={sum(1 for r in results if r.kind == ResultKind.ERROR and not r.network_error)}",
            ]
        )
    )
    logger.line(f"log_file={logger.log_path}")
    print()
    print(f"ログ: {logger.log_path}")
    if any(r.kind in {ResultKind.CONFLICT, ResultKind.ERROR} for r in results):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync",
        description="D:\\dev 配下の登録済み Git リポジトリを安全に一括同期します。",
    )
    parser.add_argument("--init", action="store_true", help="リポジトリを再検出して同期対象を選び直す")
    parser.add_argument("--scan", action="store_true", help="検出のみ（設定は変更しない）")
    parser.add_argument("--list", action="store_true", help="登録済みの同期対象を表示")
    parser.add_argument("--add", metavar="PATH", help="同期対象を追加する")
    parser.add_argument("--remove", metavar="NAME_OR_PATH", help="同期対象から外す")
    parser.add_argument("--enable", metavar="NAME", help="登録済みリポジトリを有効化")
    parser.add_argument("--disable", metavar="NAME", help="登録済みリポジトリを無効化（削除はしない）")
    parser.add_argument("--provision", action="store_true", help="GitHub リポジトリが無いプロジェクトを新規作成して同期対象に入れる")
    parser.add_argument("--yes", action="store_true", help="確認質問を省略する（--provision 用）")
    parser.add_argument("--dry-run", action="store_true", help="判定のみ。commit/merge/push しない")
    parser.add_argument("--doctor", action="store_true", help="Git / 設定 / ルートディレクトリを点検")
    parser.add_argument("--config", default=str(SCRIPT_DIR / CONFIG_FILE_NAME), help="config.json のパス")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    try:
        app = load_config(config_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        eprint(f"設定を読めません: {exc}")
        return 1

    repos_path = config_path.parent / REPOS_FILE_NAME
    log_name = now_stamp().strftime("sync-%Y%m%d-%H%M%S.log")
    logger = SyncLogger(app.log_dir / log_name)
    try:
        logger.line(f"argv={list(argv if argv is not None else sys.argv[1:])}")
        logger.line(f"root_dir={app.root_dir}")
        logger.line(f"config={config_path}")

        if args.doctor:
            return cmd_doctor(app, repos_path)

        if args.scan:
            print_banner()
            if not app.root_dir.exists():
                print(f"{app.root_dir} が存在しません。")
                return 1
            snaps = scan_snapshots(app, logger)
            if not snaps:
                print("Git リポジトリは見つかりませんでした。")
                return 0
            print_discovery_table(snaps)
            print("この検出結果はまだ同期対象ではありません。")
            print("取り込むには sync --init を実行するか、repositories.json を編集してください。")
            return 0

        if args.list:
            repos = load_repositories(repos_path)
            if not repos:
                print("repositories.json がありません。先に sync または sync --init を実行してください。")
                return 0
            print("登録済みリポジトリ:")
            for repo in repos:
                flag = "ON " if repo.enabled else "OFF"
                print(f"  [{flag}] {repo.name}  {repo.path}")
            return 0

        if args.add:
            return add_repo(repos_path, Path(args.add), app)
        if args.remove:
            return remove_repo(repos_path, args.remove)
        if args.enable:
            return set_enabled(repos_path, args.enable, True)
        if args.disable:
            return set_enabled(repos_path, args.disable, False)
        if args.provision:
            return cmd_provision(
                app,
                repos_path,
                logger,
                yes=bool(args.yes),
                dry_run=bool(args.dry_run),
            )

        setup = ensure_repos_configured(app, repos_path, logger, force_init=bool(args.init))
        if setup is None:
            return 1
        if not setup.run_sync:
            return 0
        return run_sync(app, setup.repos, logger, dry_run=bool(args.dry_run))
    finally:
        logger.close()


if __name__ == "__main__":
    sys.exit(main())

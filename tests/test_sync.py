#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""破壊的 Git 操作を使わない範囲での同期ロジック検証。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sync as syncmod  # noqa: E402
from sync import (  # noqa: E402
    AppConfig,
    Decision,
    GitClient,
    RepoConfig,
    RepoSnapshot,
    ResultKind,
    SyncLogger,
    UnsafeGitCommandError,
    assert_safe_git_args,
    decide,
    discover_repos,
    format_commit_message,
    parse_selection,
    sync_one,
)


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def init_repo(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    args = ["git", "init", "--bare" if bare else "", "-b", "main", str(path)]
    args = [a for a in args if a]
    subprocess.run(args, check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    if not bare:
        git(path, "config", "user.name", "Test User")
        git(path, "config", "user.email", "test@example.com")
        git(path, "config", "commit.gpgsign", "false")
    return path


def write_commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "-c", "commit.gpgsign=false", "commit", "-m", message)


def make_app(tmp: Path, root: Path) -> AppConfig:
    return AppConfig(
        root_dir=root,
        max_depth=2,
        auto_commit=True,
        commit_message_format="auto sync {timestamp}",
        fetch_timeout_seconds=30,
        push_timeout_seconds=30,
        log_dir=tmp / "logs",
        exclude_dir_names={"node_modules", ".git", ".venv"},
        preferred_remote="origin",
        github_owner="test-owner",
        new_repo_private=True,
        provision_non_git=True,
    )


def make_logger(tmp: Path) -> SyncLogger:
    return SyncLogger(tmp / "logs" / "test.log")


def pair_repos(tmp: Path) -> tuple[Path, Path, Path]:
    remote = init_repo(tmp / "remote.git", bare=True)
    local_a = init_repo(tmp / "project-a")
    write_commit(local_a, "README.md", "hello\n", "initial")
    git(local_a, "remote", "add", "origin", str(remote))
    git(local_a, "push", "-u", "origin", "main")
    local_b = tmp / "project-b"
    env = {**os.environ, **GIT_ENV}
    subprocess.run(
        ["git", "clone", str(remote), str(local_b)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    git(local_b, "config", "user.name", "Test User")
    git(local_b, "config", "user.email", "test@example.com")
    git(local_b, "config", "commit.gpgsign", "false")
    return remote, local_a, local_b


class SafetyTests(unittest.TestCase):
    def test_force_push_rejected(self) -> None:
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["push", "--force", "origin", "main"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["push", "-f", "origin", "HEAD"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["push", "--force-with-lease", "origin", "main"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["push", "-u", "origin", "main"])

    def test_reset_and_clean_rejected(self) -> None:
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["reset", "--hard", "HEAD"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["clean", "-fd"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["rebase", "origin/main"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["commit", "--amend", "-m", "x"])

    def test_merge_requires_ff_only(self) -> None:
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["merge", "origin/main"])
        assert_safe_git_args(["merge", "--ff-only", "origin/main"])

    def test_git_client_blocks_before_subprocess(self) -> None:
        client = GitClient(Path("."))
        with self.assertRaises(UnsafeGitCommandError):
            client.run(["push", "--force", "origin", "main"])
        self.assertEqual(client.executed, [])

    def test_remote_add_origin_allowed_other_remote_writes_blocked(self) -> None:
        assert_safe_git_args(["remote", "-v"])
        assert_safe_git_args(["remote", "add", "origin", "https://github.com/example/app.git"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["remote", "set-url", "origin", "https://example.invalid/app.git"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["remote", "remove", "origin"])
        with self.assertRaises(UnsafeGitCommandError):
            assert_safe_git_args(["init", "--bare"])


class DecisionTests(unittest.TestCase):
    def _snap(self, **kwargs: object) -> RepoSnapshot:
        data = dict(
            path=Path("/tmp/p"),
            name="p",
            valid=True,
            detached=False,
            branch="main",
            remote_name="origin",
            remote_url="https://example.invalid/p.git",
            remote_branch_exists=True,
            dirty=False,
            local_ahead=0,
            remote_ahead=0,
        )
        data.update(kwargs)
        return RepoSnapshot(**data)  # type: ignore[arg-type]

    def test_identical_skip(self) -> None:
        self.assertEqual(decide(self._snap(), True), Decision.SKIP_IDENTICAL)

    def test_remote_ahead_pull(self) -> None:
        self.assertEqual(decide(self._snap(remote_ahead=2), True), Decision.PULL_FF)

    def test_local_ahead_push(self) -> None:
        self.assertEqual(decide(self._snap(local_ahead=2), True), Decision.PUSH_LOCAL)

    def test_diverged_conflict(self) -> None:
        self.assertEqual(
            decide(self._snap(local_ahead=1, remote_ahead=1), True),
            Decision.CONFLICT_DIVERGED,
        )

    def test_dirty_and_remote_conflict(self) -> None:
        self.assertEqual(
            decide(self._snap(dirty=True, remote_ahead=1), True),
            Decision.CONFLICT_DIRTY_AND_REMOTE,
        )

    def test_dirty_commits(self) -> None:
        self.assertEqual(decide(self._snap(dirty=True), True), Decision.COMMIT_THEN_PUSH)
        self.assertEqual(decide(self._snap(dirty=True), False), Decision.ERROR_UNCOMMITTED)


class HelperTests(unittest.TestCase):
    def test_parse_selection(self) -> None:
        self.assertEqual(parse_selection("all", 4), [1, 2, 3, 4])
        self.assertEqual(parse_selection("1,3,5", 5), [1, 3, 5])
        self.assertEqual(parse_selection("2-4", 5), [2, 3, 4])
        self.assertEqual(parse_selection("q", 5), [])
        self.assertIsNone(parse_selection("9", 3))
        self.assertIsNone(parse_selection("nope", 3))

    def test_install_ps1_has_utf8_bom_and_safe_quotes(self) -> None:
        data = (ROOT / "install.ps1").read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 needs UTF-8 BOM")
        text = data.decode("utf-8-sig")
        self.assertIn("TrimEnd('\\')", text)
        self.assertNotIn('TrimEnd("\\")', text)
        self.assertNotIn("は変更していません", text)

    def test_commit_message_format(self) -> None:
        when = datetime(2026, 8, 28, 21, 7, 9)
        self.assertEqual(
            format_commit_message("auto sync {timestamp}", when),
            "auto sync 2026-08-28 21:07:09",
        )

    def test_identity_error_detection(self) -> None:
        from sync import looks_like_identity_error

        self.assertTrue(looks_like_identity_error("Author identity unknown\nPlease tell me who you are."))
        self.assertFalse(looks_like_identity_error("could not resolve host github.com"))

    def test_discover_does_not_enter_nested_git(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = init_repo(root / "project-a")
            write_commit(project, "a.txt", "a\n", "a")
            nested = init_repo(project / "vendor" / "lib")
            write_commit(nested, "b.txt", "b\n", "b")
            other = init_repo(root / "group" / "project-b")
            write_commit(other, "c.txt", "c\n", "c")
            (root / "node_modules" / "pkg").mkdir(parents=True)
            init_repo(root / "node_modules" / "pkg")
            found = {p.name for p in discover_repos(root, 2, {"node_modules", ".git"})}
            self.assertIn("project-a", found)
            self.assertIn("project-b", found)
            self.assertNotIn("lib", found)
            self.assertNotIn("pkg", found)


class IntegrationTests(unittest.TestCase):
    def test_skip_pull_push_conflict_and_dirty_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _remote, local_a, local_b = pair_repos(tmp)
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)

            skip = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(skip.kind, ResultKind.SKIP, skip.message)

            write_commit(local_a, "from-a.txt", "remote change\n", "from a")
            git(local_a, "push", "origin", "main")
            pull = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(pull.kind, ResultKind.PULL, pull.message)
            self.assertTrue((local_b / "from-a.txt").exists())

            (local_b / "local.txt").write_text("local change\n", encoding="utf-8")
            pushed = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(pushed.kind, ResultKind.PUSH, pushed.message)
            log = git(local_b, "log", "-1", "--pretty=%s").stdout.strip()
            self.assertTrue(log.startswith("auto sync "))
            git(local_a, "pull", "--ff-only", "origin", "main")
            remote_log = git(local_a, "log", "-1", "--pretty=%s").stdout.strip()
            self.assertEqual(remote_log, log)

            write_commit(local_a, "diverge-a.txt", "a\n", "diverge a")
            git(local_a, "push", "origin", "main")
            write_commit(local_b, "diverge-b.txt", "b\n", "diverge b")
            conflict = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(conflict.kind, ResultKind.CONFLICT, conflict.message)
            self.assertIn("双方", conflict.message)
            self.assertTrue((local_b / "diverge-b.txt").exists())
            self.assertFalse((local_b / "diverge-a.txt").exists())
            log_text = (tmp / "logs" / "test.log").read_text(encoding="utf-8")
            self.assertNotIn("push --force", log_text)
            self.assertNotIn("reset --hard", log_text)
            self.assertNotIn("clean -fd", log_text)

    def test_uncommitted_and_remote_does_not_pull(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _remote, local_a, local_b = pair_repos(tmp)
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            write_commit(local_a, "remote-only.txt", "x\n", "remote only")
            git(local_a, "push", "origin", "main")
            (local_b / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            result = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(result.kind, ResultKind.CONFLICT, result.message)
            self.assertFalse((local_b / "remote-only.txt").exists())
            self.assertTrue((local_b / "dirty.txt").exists())

    def test_disabled_repo_is_not_required_for_others(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _remote, _a, local_b = pair_repos(tmp)
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            missing = sync_one(RepoConfig("missing", str(tmp / "no-such"), True), app, logger)
            ok = sync_one(RepoConfig("project-b", str(local_b), True), app, logger)
            self.assertEqual(missing.kind, ResultKind.ERROR)
            self.assertEqual(ok.kind, ResultKind.SKIP)

    def test_dry_run_does_not_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _remote, _a, local_b = pair_repos(tmp)
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            (local_b / "x.txt").write_text("x\n", encoding="utf-8")
            result = sync_one(
                RepoConfig("project-b", str(local_b), True),
                app,
                logger,
                dry_run=True,
            )
            self.assertEqual(result.kind, ResultKind.PUSH)
            porcelain = git(local_b, "status", "--porcelain").stdout.strip()
            self.assertTrue(porcelain)
            log = git(local_b, "log", "-1", "--pretty=%s").stdout.strip()
            self.assertEqual(log, "initial")

    def test_first_scan_writes_disabled_repos_without_push(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _remote, _a, local_b = pair_repos(tmp)
            root = tmp / "dev"
            target = root / "sample"
            env = {**os.environ, **GIT_ENV}
            subprocess.run(
                ["git", "clone", str(_remote), str(target)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            cfg = {
                "root_dir": str(root),
                "max_depth": 2,
                "auto_commit": True,
                "commit_message_format": "auto sync {timestamp}",
                "log_dir": str(tmp / "logs"),
                "preferred_remote": "origin",
                "exclude_dir_names": [".git", "node_modules"],
            }
            config_path = tmp / "config.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            code = syncmod.main(["--config", str(config_path)])
            self.assertEqual(code, 0)
            repos_file = tmp / "repositories.json"
            self.assertTrue(repos_file.is_file())
            payload = json.loads(repos_file.read_text(encoding="utf-8"))
            self.assertTrue(payload["repositories"])
            self.assertTrue(all(not item["enabled"] for item in payload["repositories"]))


class FakeGithubApi(syncmod.GithubApi):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.created: list[str] = []
        self.existing: dict[str, syncmod.GithubRepoInfo] = {}

    def current_login(self) -> str:
        return "test-owner"

    def inspect(self, owner: str, name: str) -> syncmod.GithubRepoInfo:
        return self.existing.get(name, syncmod.GithubRepoInfo(exists=False))

    def create(self, owner: str, name: str, private: bool) -> str:
        bare = init_repo(self.root / f"{name}.git", bare=True)
        url = str(bare)
        self.created.append(name)
        self.existing[name] = syncmod.GithubRepoInfo(exists=True, empty=True, url=url)
        return url


class ProvisionTests(unittest.TestCase):
    def test_provision_creates_remote_only_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            local = init_repo(tmp / "orphan-app")
            write_commit(local, "README.md", "hello\n", "initial")
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            api = FakeGithubApi(tmp / "gh")
            candidate = syncmod.ProvisionCandidate(
                path=local,
                name="orphan-app",
                kind="no_remote",
                github_name="orphan-app",
            )
            result = syncmod.provision_one(candidate, app, api, "test-owner", logger, dry_run=False)
            self.assertEqual(result.kind, ResultKind.CREATE, result.message)
            self.assertEqual(api.created, ["orphan-app"])
            remotes = git(local, "remote", "-v").stdout
            self.assertIn("origin", remotes)
            git(local, "fetch", "origin")
            self.assertTrue(git(local, "rev-parse", "origin/main").stdout.strip())

            again = syncmod.provision_one(candidate, app, api, "test-owner", logger, dry_run=False)
            self.assertEqual(again.kind, ResultKind.ERROR)
            self.assertIn("すでに remote", again.message)
            self.assertEqual(api.created, ["orphan-app"])

    def test_provision_inits_non_git_folder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project = tmp / "plain-app"
            project.mkdir()
            (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            api = FakeGithubApi(tmp / "gh")
            candidate = syncmod.ProvisionCandidate(
                path=project,
                name="plain-app",
                kind="not_git",
                github_name="plain-app",
            )
            result = syncmod.provision_one(candidate, app, api, "test-owner", logger, dry_run=False)
            self.assertEqual(result.kind, ResultKind.CREATE, result.message)
            self.assertTrue((project / ".git").exists())
            self.assertEqual(api.created, ["plain-app"])

    def test_provision_does_not_attach_non_empty_existing_github(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            local = init_repo(tmp / "taken-app")
            write_commit(local, "README.md", "hello\n", "initial")
            app = make_app(tmp, tmp)
            logger = make_logger(tmp)
            self.addCleanup(logger.close)
            api = FakeGithubApi(tmp / "gh")
            api.existing["taken-app"] = syncmod.GithubRepoInfo(
                exists=True,
                empty=False,
                url="https://github.com/test-owner/taken-app.git",
            )
            candidate = syncmod.ProvisionCandidate(
                path=local,
                name="taken-app",
                kind="no_remote",
                github_name="taken-app",
            )
            result = syncmod.provision_one(candidate, app, api, "test-owner", logger, dry_run=False)
            self.assertEqual(result.kind, ResultKind.ERROR)
            self.assertIn("空ではありません", result.message)
            remotes = git(local, "remote", "-v").stdout
            self.assertNotIn("origin", remotes)
            self.assertEqual(api.created, [])

    def test_discover_non_git_direct_children_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "plain").mkdir()
            init_repo(root / "already-git")
            (root / "node_modules").mkdir()
            found = {p.name for p in syncmod.discover_non_git_projects(root, {"node_modules", ".git"})}
            self.assertIn("plain", found)
            self.assertNotIn("already-git", found)
            self.assertNotIn("node_modules", found)


if __name__ == "__main__":
    unittest.main()

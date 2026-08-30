#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同期通知メールの共通処理。

開始・終了・テスト・起動失敗は、すべてここを通す。
sync.py からも、Python が PATH に無いときのフォールバックからも同じ関数を使う。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import smtplib
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

Attachment = tuple[str, bytes]

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_NAME = "config.json"
NOTIFY_LOCAL_FILE_NAME = "notify.local.json"
SMTP_PASSWORD_ENV = "CLOUD_AGENT_SYNC_SMTP_PASSWORD"
SUBJECT_PREFIX = "[cloud-agent-sync]"


class NotifyEvent(str, Enum):
    START = "start"
    END = "end"
    TEST = "test"
    PYTHON_MISSING = "python_missing"


@dataclass
class NotifySettings:
    enabled: bool = True
    to_email: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.to_email
            and self.smtp_host
            and self.smtp_user
            and self.smtp_password
        )


@dataclass
class NotifyCounts:
    success: int = 0
    skip: int = 0
    conflict: int = 0
    network: int = 0
    error: int = 0


@dataclass
class NotifyMessage:
    subject: str
    body: str


MailSender = Callable[[NotifySettings, str, str], tuple[bool, str]]


def _read_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _git_user_email() -> str:
    try:
        completed = subprocess.run(
            ["git", "config", "--global", "--get", "user.email"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def load_settings(config_path: Path, *, git_email: str | None = None) -> NotifySettings:
    raw: dict = {}
    if config_path.is_file():
        loaded = _read_json_object(config_path)
        raw.update(loaded)
    local = _read_json_object(config_path.parent / NOTIFY_LOCAL_FILE_NAME)
    to_email = str(local.get("notify_email", raw.get("notify_email", ""))).strip()
    if not to_email:
        to_email = (git_email if git_email is not None else _git_user_email()).strip()
    smtp_host = str(local.get("smtp_host", raw.get("smtp_host", "smtp.gmail.com"))).strip() or "smtp.gmail.com"
    smtp_port = int(local.get("smtp_port", raw.get("smtp_port", 587)))
    smtp_user = str(local.get("smtp_user", raw.get("smtp_user", ""))).strip() or to_email
    smtp_password = str(
        os.environ.get(SMTP_PASSWORD_ENV)
        or local.get("smtp_password")
        or raw.get("smtp_password")
        or ""
    ).strip().replace(" ", "")
    if "notify_on_sync" in local:
        enabled = bool(local.get("notify_on_sync"))
    else:
        enabled = bool(raw.get("notify_on_sync", True))
    return NotifySettings(
        enabled=enabled,
        to_email=to_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )


def notify_setup_hint(example_dir: Path | None = None) -> list[str]:
    example = (example_dir or SCRIPT_DIR) / "notify.local.example.json"
    return [
        "[警告] 通知メールを送れません。Gmail のアプリパスワードが必要です。",
        f"  1. {example} を notify.local.json にコピー",
        "  2. Google アカウント → セキュリティ → 2段階認証 → アプリパスワード",
        "  3. notify.local.json の smtp_password にアプリパスワードを書く",
        "  4. sync --notify-test で送信テスト",
    ]


def _now_text(when: datetime | None = None) -> str:
    stamp = when or datetime.now().astimezone()
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


def _host_name() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "(unknown)"


def format_subject(
    event: NotifyEvent | str,
    *,
    counts: NotifyCounts | None = None,
    crash: str = "",
    note: str = "",
    project: str = "",
) -> str:
    kind = NotifyEvent(event)
    if kind is NotifyEvent.START:
        if project.strip():
            return f"{SUBJECT_PREFIX} {project.strip()} を開始しました"
        return f"{SUBJECT_PREFIX} 同期を開始しました"
    if kind is NotifyEvent.PYTHON_MISSING:
        return f"{SUBJECT_PREFIX} 同期失敗（Python なし）"
    if kind is NotifyEvent.TEST:
        return f"{SUBJECT_PREFIX} 送信テスト"
    if crash:
        if project.strip():
            return f"{SUBJECT_PREFIX} {project.strip()} 失敗（途中で停止）"
        return f"{SUBJECT_PREFIX} 同期失敗（途中で停止）"
    stats = counts or NotifyCounts()
    if stats.conflict or stats.error or stats.network:
        parts: list[str] = []
        if stats.conflict:
            parts.append(f"コンフリクト{stats.conflict}")
        if stats.network:
            parts.append(f"接続エラー{stats.network}")
        if stats.error:
            parts.append(f"エラー{stats.error}")
        head = f"{SUBJECT_PREFIX} {project.strip()} 要確認" if project.strip() else f"{SUBJECT_PREFIX} 要確認"
        return head + " " + " ".join(parts)
    if note and stats.success == 0 and stats.skip == 0:
        if project.strip():
            return f"{SUBJECT_PREFIX} {project.strip()} 実行なし（要確認）"
        return f"{SUBJECT_PREFIX} 同期なし（要確認）"
    if project.strip():
        return f"{SUBJECT_PREFIX} {project.strip()} 完了"
    return f"{SUBJECT_PREFIX} 同期完了"


def format_body(
    event: NotifyEvent | str,
    *,
    counts: NotifyCounts | None = None,
    details: Sequence[str] | None = None,
    log_path: Path | str | None = None,
    crash: str = "",
    note: str = "",
    dry_run: bool = False,
    when: datetime | None = None,
    project: str = "",
) -> str:
    kind = NotifyEvent(event)
    stamp = _now_text(when)
    host = _host_name()
    name = project.strip() or "cloud-agent-sync"
    if kind is NotifyEvent.START:
        lines = [
            f"{name} を開始しました。" if project.strip() else "cloud-agent-sync が同期を開始しました。",
            "",
            f"日時: {stamp}",
            f"ホスト: {host}",
            f"プロジェクト: {name}",
        ]
        if log_path:
            lines.append(f"ログ: {log_path}")
        lines.append("")
        lines.append("終了時にもう一通、結果メールを送ります。エラーでも必ず送ります。")
        return "\n".join(lines) + "\n"
    if kind is NotifyEvent.PYTHON_MISSING:
        lines = [
            "cloud-agent-sync が起動できませんでした。",
            "",
            f"日時: {stamp}",
            f"ホスト: {host}",
            "",
            note or "Python 3 が見つかりません。",
            "",
            "Python 3 をインストールし、PATH を通してから再実行してください。",
        ]
        return "\n".join(lines) + "\n"

    stats = counts or NotifyCounts()
    lines = [
        f"{name} の実行結果です。" if project.strip() else "cloud-agent-sync の実行結果です。",
        "",
        f"日時: {stamp}",
        f"ホスト: {host}",
        f"プロジェクト: {name}",
    ]
    if kind is NotifyEvent.TEST:
        lines.append("モード: 送信テスト（同期はしていません）")
    if dry_run:
        lines.append("モード: dry-run（commit / merge / push なし）")
    if log_path:
        lines.append(f"ログ: {log_path}")
    lines.append("")
    lines.append("----- 集計 -----")
    lines.append(f"同期成功：{stats.success}件")
    lines.append(f"変更なし：{stats.skip}件")
    lines.append(f"コンフリクト：{stats.conflict}件")
    lines.append(f"接続エラー：{stats.network}件")
    lines.append(f"その他エラー：{stats.error}件")
    if note:
        lines.append("")
        lines.append(note)
    if crash:
        lines.append("")
        lines.append("----- 例外 -----")
        lines.append(crash.strip())
    if details:
        lines.append("")
        lines.append("----- 詳細 -----")
        lines.extend(details)
    lines.append("")
    lines.append("成功でも失敗でも、毎回このメールを送ります。")
    return "\n".join(lines) + "\n"


def build_message(
    event: NotifyEvent | str,
    *,
    counts: NotifyCounts | None = None,
    details: Sequence[str] | None = None,
    log_path: Path | str | None = None,
    crash: str = "",
    note: str = "",
    dry_run: bool = False,
    when: datetime | None = None,
    project: str = "",
) -> NotifyMessage:
    return NotifyMessage(
        subject=format_subject(event, counts=counts, crash=crash, note=note, project=project),
        body=format_body(
            event,
            counts=counts,
            details=details,
            log_path=log_path,
            crash=crash,
            note=note,
            dry_run=dry_run,
            when=when,
            project=project,
        ),
    )


def build_email(
    settings: NotifySettings,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.to_email
    msg.set_content(body)
    for filename, data in attachments or ():
        name = Path(str(filename or "attachment.bin")).name or "attachment.bin"
        ctype, _encoding = mimetypes.guess_type(name)
        if ctype:
            maintype, subtype = ctype.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def smtp_send(
    settings: NotifySettings,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] | None = None,
) -> tuple[bool, str]:
    if not settings.ready():
        return False, "notify not configured"
    msg = build_email(settings, subject, body, attachments)
    try:
        if int(settings.smtp_port) == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        return True, ""
    except (OSError, smtplib.SMTPException) as exc:
        text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return False, text


def send(
    settings: NotifySettings,
    event: NotifyEvent | str,
    *,
    counts: NotifyCounts | None = None,
    details: Sequence[str] | None = None,
    log_path: Path | str | None = None,
    crash: str = "",
    note: str = "",
    dry_run: bool = False,
    sender: MailSender | None = None,
    log_line: Callable[[str], None] | None = None,
    project: str = "",
    subject: str = "",
) -> bool:
    """開始・終了・テスト・起動失敗のすべてが使う送信入口。"""
    if not settings.enabled:
        return False
    if not settings.ready():
        for line in notify_setup_hint():
            print(line)
        if log_line:
            log_line("NOTIFY skipped (password or address missing)")
        return False
    message = build_message(
        event,
        counts=counts,
        details=details,
        log_path=log_path,
        crash=crash,
        note=note,
        dry_run=dry_run,
        project=project,
    )
    if subject.strip():
        message.subject = subject.strip()
    ok, err = (sender or smtp_send)(settings, message.subject, message.body)
    if ok:
        print(f"通知メールを送信しました: {settings.to_email}  ({message.subject})")
        if log_line:
            log_line(f"NOTIFY sent to={settings.to_email} subject={message.subject}")
        return True
    print(f"通知メールの送信に失敗しました: {err}")
    if log_line:
        log_line(f"NOTIFY failed {err}")
    return False


def home_settings() -> NotifySettings:
    """パスワードは cloud-agent-sync の notify.local.json だけを読む。呼び元プロジェクトには置かない。"""
    settings = load_settings(SCRIPT_DIR / CONFIG_FILE_NAME)
    local = _read_json_object(SCRIPT_DIR / NOTIFY_LOCAL_FILE_NAME)
    settings.smtp_password = str(local.get("smtp_password") or "").strip().replace(" ", "")
    return settings


def notify_note(
    project: str,
    subject: str,
    body: str,
    *,
    attachments: Sequence[Attachment] | None = None,
    sender: MailSender | None = None,
    settings: NotifySettings | None = None,
    log_line: Callable[[str], None] | None = None,
) -> bool:
    """集計なしの単純通知。添付可。設定は notify.local.json。"""
    cfg = settings or home_settings()
    if not cfg.enabled:
        return False
    if not cfg.ready():
        for line in notify_setup_hint():
            print(line)
        if log_line:
            log_line("NOTIFY skipped (password or address missing)")
        return False
    title = (subject or "").strip() or f"{SUBJECT_PREFIX} {(project or '').strip() or 'note'}"
    if sender is not None:
        ok, err = sender(cfg, title, body)
    else:
        ok, err = smtp_send(cfg, title, body, attachments)
    if ok:
        print(f"通知メールを送信しました: {cfg.to_email}  ({title})")
        if log_line:
            log_line(f"NOTIFY sent to={cfg.to_email} subject={title}")
        return True
    print(f"通知メールの送信に失敗しました: {err}")
    if log_line:
        log_line(f"NOTIFY failed {err}")
    return False


def notify_job(
    project: str,
    event: NotifyEvent | str = NotifyEvent.END,
    *,
    counts: NotifyCounts | None = None,
    note: str = "",
    crash: str = "",
    details: Sequence[str] | None = None,
    log_path: Path | str | None = None,
    sender: MailSender | None = None,
    settings: NotifySettings | None = None,
    subject: str = "",
) -> bool:
    """他の Python プロジェクト用。設定・パスワードは常にこの notify.py の置き場所を使う。"""
    name = (project or "").strip() or "project"
    return send(
        settings or home_settings(),
        event,
        counts=counts,
        note=note,
        crash=crash,
        details=details,
        log_path=log_path,
        sender=sender,
        project=name,
        subject=subject,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="cloud-agent-sync の通知メールを送る")
    parser.add_argument(
        "--event",
        required=True,
        choices=[item.value for item in NotifyEvent],
        help="start / end / test / python_missing",
    )
    parser.add_argument("--note", default="", help="本文に足すメモ")
    parser.add_argument("--project", default="", help="他プロジェクト名（例: line-stamp-auto）。空なら Git 同期メール")
    parser.add_argument("--log", default="", help="ログファイルのパス")
    parser.add_argument("--config", default=str(SCRIPT_DIR / CONFIG_FILE_NAME))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.project:
        ok = notify_job(
            args.project,
            args.event,
            note=args.note,
            log_path=args.log or None,
        )
        return 0 if ok else 1
    settings = load_settings(Path(args.config))
    ok = send(
        settings,
        args.event,
        note=args.note,
        log_path=args.log or None,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append this repo's bin directory to the current user's PATH.

Does not rewrite the whole PATH, and does not touch the PowerShell profile.
Windows only. Used as a fallback when install.ps1 cannot be parsed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).rstrip("\\/")


def main() -> int:
    if os.name != "nt":
        print("This installer is for Windows.")
        return 1

    import ctypes
    import winreg

    root = Path(__file__).resolve().parent
    bin_dir = str(root / "bin")
    if not (root / "bin").is_dir():
        print("bin directory is missing:", bin_dir)
        return 1

    print("cloud-agent-sync installer")
    print("Root:", root)
    print()

    access = winreg.KEY_READ | winreg.KEY_WRITE
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access)
    try:
        try:
            user_path, reg_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            user_path, reg_type = "", winreg.REG_EXPAND_SZ
        if not isinstance(user_path, str):
            user_path = str(user_path or "")

        parts = [p.strip() for p in user_path.split(";") if p.strip()]
        already = any(_norm(part) == _norm(bin_dir) for part in parts)
        if already:
            print("PATH: already registered:", bin_dir)
        else:
            new_path = bin_dir if not user_path.strip() else user_path.rstrip(";") + ";" + bin_dir
            winreg.SetValueEx(key, "Path", 0, reg_type or winreg.REG_EXPAND_SZ, new_path)
            print("PATH: appended to the user PATH")
            print("     ", bin_dir)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF,
                0x001A,
                0,
                "Environment",
                0x0002,
                5000,
                None,
            )
    finally:
        key.Close()

    current = os.environ.get("Path") or os.environ.get("PATH") or ""
    if _norm(bin_dir) not in _norm(current):
        os.environ["Path"] = current.rstrip(";") + ";" + bin_dir

    print()
    print("PowerShell Profile was not changed.")
    print("Existing Git remotes, branches, and credentials were not changed.")
    print("Next: restart Cursor and the terminal, then run sync --doctor")
    print("Install finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

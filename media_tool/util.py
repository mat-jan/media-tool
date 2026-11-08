"""Małe, niezależne od platformy narzędzia pomocnicze."""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CmdResult:
    rc: int
    out: str = ""
    err: str = ""
    found: bool = True

    @property
    def ok(self) -> bool:
        return self.found and self.rc == 0


def run(cmd: list, timeout: float = 30, binary: bool = False):
    """Uruchamia polecenie bez powłoki. Nigdy nie rzuca wyjątku z powodu polecenia."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return CmdResult(127, "", f"nie znaleziono: {cmd[0]}", found=False)
    except (OSError, subprocess.SubprocessError) as e:
        return CmdResult(126, "", str(e))
    if binary:
        return CmdResult(p.returncode, p.stdout, p.stderr.decode("utf-8", "replace"))
    return CmdResult(p.returncode, p.stdout.decode("utf-8", "replace"),
                     p.stderr.decode("utf-8", "replace"))


def run_json(cmd: list, timeout: float = 30, accept_nonzero: bool = False) -> Optional[Any]:
    r = run(cmd, timeout)
    if not r.found or not (r.ok or accept_nonzero) or not r.out.strip():
        return None
    try:
        return json.loads(r.out)
    except ValueError:
        return None


def run_plist(cmd: list, timeout: float = 30) -> Optional[Any]:
    r = run(cmd, timeout, binary=True)
    if not r.ok or not r.out:
        return None
    try:
        return plistlib.loads(r.out)
    except Exception:
        return None


def human_size(n: Optional[int]) -> str:
    if n is None:
        return "NIEDOSTĘPNE"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if v < 1000 or unit == "PB":
            return f"{int(v)} B" if unit == "B" else f"{v:.2f} {unit}"
        v /= 1000
    return str(n)


def mask(value: Optional[str], keep: int = 2) -> Optional[str]:
    """Maskuje identyfikator (np. numer seryjny) do udostępniania raportów."""
    if not value:
        return value
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


def atomic_write_text(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def is_admin() -> bool:
    """Czy proces ma uprawnienia administratora/root (potrzebne do odczytu surowych urządzeń)."""
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def disable_cache(fd: int) -> bool:
    """Najlepsza próba ominięcia pamięci podręcznej systemu przy odczycie."""
    try:
        if sys.platform == "darwin":
            import fcntl
            fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
            return True
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            return True
    except OSError:
        pass
    return False

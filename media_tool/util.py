"""Małe, niezależne od platformy narzędzia pomocnicze."""
from __future__ import annotations

import json
import os
import plistlib
import shutil
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


# Katalogi z narzędziami systemowymi, których może brakować w PATH (sudo, uruchomienie z GUI/launchera).
_EXTRA_DIRS = {
    "darwin": ["/usr/sbin", "/sbin", "/usr/local/sbin", "/usr/local/bin", "/opt/homebrew/bin", "/opt/homebrew/sbin"],
    "linux": ["/usr/sbin", "/sbin", "/usr/local/sbin", "/usr/local/bin", "/usr/bin", "/bin"],
}


def search_path() -> str:
    """PATH uzupełniony o standardowe katalogi systemowe (bez duplikatów, z zachowaniem kolejności)."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    key = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else ""
    parts += [d for d in _EXTRA_DIRS.get(key, []) if d not in parts]
    return os.pathsep.join(parts)


def which(name: str):
    """shutil.which z rozszerzonym PATH; na Windows dodatkowo znany katalog PowerShella."""
    found = shutil.which(name, path=search_path())
    if found or sys.platform != "win32" or name.lower() not in ("powershell", "powershell.exe"):
        return found
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return cand if os.path.exists(cand) else None


def run(cmd: list, timeout: float = 30, binary: bool = False):
    """Uruchamia polecenie bez powłoki. Nigdy nie rzuca wyjątku z powodu polecenia."""
    exe = which(cmd[0]) if cmd and not os.path.isabs(cmd[0]) else None
    if exe:
        cmd = [exe] + list(cmd[1:])
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           env={**os.environ, "PATH": search_path()})
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


def chown_to_invoking_user(path: str) -> None:
    """Przy uruchomieniu przez sudo pliki mają należeć do zwykłego użytkownika, nie do root-a
    (inaczej nie dałoby się ich potem usunąć/edytować bez sudo). Poza sudo – nic nie robi."""
    if sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
    if uid.isdigit() and gid.isdigit():
        try:
            os.chown(path, int(uid), int(gid))
        except OSError:
            pass


def makedirs_owned(path: str) -> None:
    """os.makedirs, ale nowo utworzone katalogi też trafiają do użytkownika (patrz wyżej)."""
    missing = []
    d = os.path.abspath(path)
    while d and not os.path.exists(d):
        missing.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    os.makedirs(path, exist_ok=True)
    for m in missing:
        chown_to_invoking_user(m)


def atomic_write_text(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    makedirs_owned(d)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        chown_to_invoking_user(path)
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


def ensure_utf8_streams() -> None:
    """Konsola Windows bywa w cp1250/cp852 – polskie znaki i symbole ✔ nie mogą wywalać programu."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

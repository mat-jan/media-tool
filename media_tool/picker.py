"""Okna wyboru folderu/pliku (systemowe), bez dodatkowych pakietów.

macOS: osascript (okno Findera) · Windows: PowerShell + System.Windows.Forms · Linux: zenity lub kdialog.
Gdy okno jest niedostępne (SSH, brak ekranu, brak narzędzia) – `gui_available()` zwraca False i wywołujący
wraca do wpisywania ścieżki.
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

from .util import run, which


class PickerUnavailable(Exception):
    """Okna wyboru nie da się użyć (brak narzędzia, błąd) – wywołujący powinien zapytać o ścieżkę tekstowo."""


def gui_available() -> bool:
    if os.environ.get("MEDIA_TOOL_NO_GUI") == "1":
        return False
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION") and bool(which("osascript"))
    if sys.platform == "win32":
        return bool(which("powershell") or which("pwsh"))
    if sys.platform.startswith("linux"):
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        return has_display and bool(which("zenity") or which("kdialog"))
    return False


def _existing_dir(path: Optional[str]) -> Optional[str]:
    """Najbliższy istniejący katalog (okna wymagają istniejącej lokalizacji startowej)."""
    if not path:
        return None
    p = os.path.abspath(os.path.expanduser(path))
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return p or None


def _as_string(s: str) -> str:
    """Bezpieczny literał tekstowy AppleScript."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def applescript(kind: str, title: str, start: Optional[str]) -> str:
    verb = "choose folder" if kind == "folder" else "choose file"
    loc = f" default location (POSIX file {_as_string(start)} as alias)" if start else ""
    return ("tell application (path to frontmost application as text)\n"
            f"    set theItem to {verb} with prompt {_as_string(title)}{loc}\n"
            "end tell\n"
            "return POSIX path of theItem")


def powershell_script(kind: str, title: str, start: Optional[str]) -> str:
    q = lambda s: "'" + s.replace("'", "''") + "'"
    head = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "Add-Type -AssemblyName System.Windows.Forms\n"
            "[System.Windows.Forms.Application]::EnableVisualStyles()\n"
            "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}\n")
    if kind == "folder":
        body = ("$d = New-Object System.Windows.Forms.FolderBrowserDialog\n"
                f"$d.Description = {q(title)}\n$d.ShowNewFolderButton = $true\n"
                + (f"$d.SelectedPath = {q(start)}\n" if start else "")
                + "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
                  "{ [Console]::Out.Write($d.SelectedPath) } else { exit 1 }")
    else:
        body = ("$d = New-Object System.Windows.Forms.OpenFileDialog\n"
                f"$d.Title = {q(title)}\n"
                + (f"$d.InitialDirectory = {q(start)}\n" if start else "")
                + "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
                  "{ [Console]::Out.Write($d.FileName) } else { exit 1 }")
    return head + body


def _clean(out: str) -> Optional[str]:
    p = out.strip("\r\n")
    if not p:
        return None
    if len(p) > 1 and p.endswith(("/", "\\")) and not (len(p) == 3 and p[1] == ":"):
        p = p.rstrip("/\\") or p[:1]
    return p


def _pick(kind: str, title: str, start: Optional[str]) -> Optional[str]:
    """Zwraca ścieżkę, None gdy użytkownik anulował; PickerUnavailable gdy okna nie da się otworzyć."""
    start = _existing_dir(start)
    if sys.platform == "darwin":
        r = run(["osascript", "-e", applescript(kind, title, start)], timeout=3600)
        if r.ok:
            return _clean(r.out)
        if "-128" in r.err or "User canceled" in r.err:
            return None
        raise PickerUnavailable(r.err.strip() or "osascript nie zadziałał")
    if sys.platform == "win32":
        exe = which("powershell") or which("pwsh")
        if not exe:
            raise PickerUnavailable("brak PowerShella")
        r = run([exe, "-NoProfile", "-STA", "-Command", powershell_script(kind, title, start)], timeout=3600)
        if r.ok:
            return _clean(r.out)
        if r.rc == 1 and not r.err.strip():
            return None
        raise PickerUnavailable(r.err.strip() or "okno wyboru nie zadziałało")
    cmd: List[str]
    if which("zenity"):
        cmd = ["zenity", "--file-selection", f"--title={title}"] + (["--directory"] if kind == "folder" else [])
        if start:
            cmd.append(f"--filename={start.rstrip('/')}/")
    elif which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory" if kind == "folder" else "--getopenfilename", start or os.path.expanduser("~"),
               "--title", title]
    else:
        raise PickerUnavailable("brak zenity/kdialog")
    r = run(cmd, timeout=3600)
    if r.ok:
        return _clean(r.out)
    if r.rc == 1:
        return None
    raise PickerUnavailable(r.err.strip() or "okno wyboru nie zadziałało")


def pick_folder(title: str, start: Optional[str] = None) -> Optional[str]:
    return _pick("folder", title, start)


def pick_file(title: str, start: Optional[str] = None) -> Optional[str]:
    return _pick("file", title, start)

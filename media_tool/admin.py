"""Ponowne uruchomienie programu z uprawnieniami administratora (potrzebnymi do odczytu surowych urządzeń)."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from .util import is_admin, which

START_ITEM_ENV = "MEDIA_TOOL_START_ITEM"
_PASS_ENV = ("PYTHONPATH", "MEDIA_TOOL_HOME", "MEDIA_TOOL_OUTPUT_DIR", "NO_COLOR", "MEDIA_TOOL_ASCII")


def can_elevate() -> bool:
    if is_admin():
        return False
    return sys.platform == "win32" or bool(which("sudo"))


def program_command() -> List[str]:
    """Polecenie, które uruchamia ten sam program od nowa (binarka, .pyz albo `python -m media_tool`)."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + sys.argv[1:]
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and getattr(spec, "name", None):
        name = spec.name[: -len(".__main__")] if spec.name.endswith(".__main__") else spec.name
        return [sys.executable, "-m", name] + sys.argv[1:]
    return [sys.executable] + sys.argv


def elevation_argv(start_item: Optional[int] = None) -> List[str]:
    """Pełne polecenie `sudo env … program` (macOS/Linux)."""
    env = {"HOME": os.path.expanduser("~")}
    env.update({k: os.environ[k] for k in _PASS_ENV if k in os.environ})
    if start_item:
        env[START_ITEM_ENV] = str(start_item)
    return ["sudo", "env"] + [f"{k}={v}" for k, v in env.items()] + program_command()


def relaunch_as_admin(start_item: Optional[int] = None) -> None:
    """macOS/Linux: zastępuje bieżący proces `sudo …` (zapyta o hasło w tym samym terminalu) – nie wraca.
    Windows: otwiera nowe okno z prośbą UAC i kończy bieżący proces. Rzuca OSError, gdy się nie uda."""
    if sys.platform == "win32":
        import ctypes
        cmd = program_command()
        env_note = {START_ITEM_ENV: str(start_item)} if start_item else {}
        os.environ.update(env_note)          # dziedziczone przez proces uruchomiony przez ShellExecute
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]), None, 1)
        if ret <= 32:
            raise OSError("Nie udało się uzyskać uprawnień administratora (anulowano UAC?)")
        sys.exit(0)
    argv = elevation_argv(start_item)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(argv[0], argv)

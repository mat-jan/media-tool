"""Wybór backendu dla bieżącego systemu operacyjnego."""
from __future__ import annotations

import sys

from .base import Backend, BackendError


def get_backend(platform: str = None) -> Backend:
    platform = platform or sys.platform
    if platform == "darwin":
        from .macos import MacOSBackend
        return MacOSBackend()
    if platform.startswith("linux"):
        from .linux import LinuxBackend
        return LinuxBackend()
    if platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    raise BackendError(f"Nieobsługiwany system: {platform}")


__all__ = ["Backend", "BackendError", "get_backend"]

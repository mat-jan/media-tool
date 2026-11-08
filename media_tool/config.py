"""Konfiguracja programu (plik JSON w katalogu użytkownika)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from .util import atomic_write_json

# klucz -> (domyślna wartość, opis)
SCHEMA: Dict[str, tuple] = {
    "chunk_size_mib": (4, "Rozmiar bloku odczytu przy obrazowaniu/skanowaniu (MiB, 1-256)"),
    "max_read_errors": (1000, "Po tylu nieczytelnych sektorach obrazowanie/skan zostaje przerwane"),
    "benchmark_size_mib": (256, "Ilość danych czytanych w teście prędkości (MiB)"),
    "report_dir": (".", "Domyślny katalog zapisu raportów"),
    "redact_serials": (False, "Maskuj numery seryjne w raportach (do bezpiecznego udostępniania)"),
    "confirm_read_ops": (True, "Pytaj o potwierdzenie przed operacjami tylko do odczytu"),
    "log_level": ("INFO", "Poziom logowania: DEBUG, INFO, WARNING, ERROR"),
    "hash_algorithm": ("sha256", "Domyślny algorytm sum kontrolnych: md5, sha1, sha256, sha512, blake2b"),
}
_RANGES = {"chunk_size_mib": (1, 256), "max_read_errors": (1, 10_000_000), "benchmark_size_mib": (1, 65536)}
_CHOICES = {"log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
            "hash_algorithm": ("md5", "sha1", "sha256", "sha512", "blake2b")}


class ConfigError(ValueError):
    pass


def _platform_dirs():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/media-tool", home / "Library/Logs/media-tool"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData/Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local")
        return base / "media-tool", local / "media-tool" / "logs"
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "media-tool"
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state") / "media-tool"
    return cfg, state


def config_dir() -> Path:
    override = os.environ.get("MEDIA_TOOL_HOME")
    return Path(override) if override else _platform_dirs()[0]


def log_dir() -> Path:
    override = os.environ.get("MEDIA_TOOL_HOME")
    return Path(override) / "logs" if override else _platform_dirs()[1]


def config_path() -> Path:
    return config_dir() / "config.json"


def defaults() -> Dict[str, Any]:
    return {k: v[0] for k, v in SCHEMA.items()}


def load() -> Dict[str, Any]:
    cfg = defaults()
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if k in SCHEMA:
                    try:
                        cfg[k] = coerce(k, v)
                    except ConfigError:
                        pass  # zła wartość w pliku -> zostaje domyślna
    except (OSError, ValueError):
        pass
    return cfg


def coerce(key: str, raw: Any) -> Any:
    if key not in SCHEMA:
        raise ConfigError(f"Nieznany klucz: {key}. Dostępne: {', '.join(SCHEMA)}")
    default = SCHEMA[key][0]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "true", "tak", "yes", "on"):
            return True
        if s in ("0", "false", "nie", "no", "off"):
            return False
        raise ConfigError(f"{key}: oczekiwano tak/nie")
    if isinstance(default, int):
        try:
            val = int(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{key}: oczekiwano liczby całkowitej")
        lo, hi = _RANGES.get(key, (None, None))
        if lo is not None and not lo <= val <= hi:
            raise ConfigError(f"{key}: dozwolony zakres {lo}-{hi}")
        return val
    val = str(raw).strip()
    if key in _CHOICES:
        val = val.upper() if key == "log_level" else val.lower()
        if val not in _CHOICES[key]:
            raise ConfigError(f"{key}: dozwolone wartości: {', '.join(_CHOICES[key])}")
    return val


def set_value(key: str, raw: Any) -> Any:
    val = coerce(key, raw)
    cfg = load()
    cfg[key] = val
    atomic_write_json(str(config_path()), cfg)
    return val


def reset() -> None:
    try:
        config_path().unlink()
    except FileNotFoundError:
        pass

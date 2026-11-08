"""Macierz funkcji: co jest dostępne na danym systemie i dlaczego (lub dlaczego nie)."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Dict, List

from .util import is_admin

OK, PARTIAL, NO = "dostępne", "częściowo", "niedostępne"


@dataclass
class Feature:
    key: str
    title: str
    status: str
    reason: str = ""

    @property
    def mark(self) -> str:
        return {OK: "✔", PARTIAL: "◐", NO: "✖"}[self.status]


def features(os_name: str = None) -> Dict[str, Feature]:
    os_name = os_name or sys.platform
    mac, linux, win = os_name == "darwin", os_name.startswith("linux"), os_name == "win32"
    admin = is_admin()
    smartctl = bool(shutil.which("smartctl"))
    F: Dict[str, Feature] = {}

    def add(key, title, status=OK, reason=""):
        F[key] = Feature(key, title, status, reason)

    if linux and not shutil.which("lsblk"):
        base_status, base_reason = NO, "brak lsblk (pakiet util-linux)"
    elif win and not (shutil.which("powershell") or shutil.which("pwsh")):
        base_status, base_reason = NO, "brak PowerShell"
    elif mac and not shutil.which("diskutil"):
        base_status, base_reason = NO, "brak diskutil"
    else:
        base_status, base_reason = OK, ""
    for key, title in (("list", "Wykrywanie i lista nośników"), ("inspect", "Szczegółowa identyfikacja"),
                       ("serial", "Numery seryjne"), ("ids", "Identyfikatory sprzętowe"),
                       ("uuids", "UUID i identyfikatory systemu plików"), ("partitions", "Partycje i woluminy"),
                       ("mounts", "Punkty montowania"), ("mounted", "Status zamontowania"),
                       ("system", "Status dysku systemowego"), ("space", "Wolne miejsce")):
        add(key, title, base_status, base_reason)

    if smartctl:
        add("health", "SMART / health", OK if (admin or win) else PARTIAL,
            "" if (admin or win) else "smartctl zwykle wymaga uprawnień administratora")
    elif linux:
        add("health", "SMART / health", NO, "zainstaluj smartmontools (smartctl)")
    else:
        add("health", "SMART / health", PARTIAL, "bez smartctl: tylko ogólny status z systemu")

    raw_reason = "" if admin else "odczyt surowego urządzenia wymaga uprawnień administratora (sudo)"
    raw_status = OK if admin else PARTIAL
    add("benchmark", "Test prędkości odczytu", raw_status,
        raw_reason and "bez uprawnień: odczyt istniejących plików z zamontowanego woluminu")
    add("integrity", "Test integralności obszarów", raw_status, raw_reason)
    add("scan", "Skanowanie tylko do odczytu", raw_status, raw_reason)
    add("errors", "Sprawdzenie błędów odczytu", raw_status, raw_reason)
    add("image_create", "Tworzenie obrazu", raw_status, raw_reason)
    add("image_verify", "Weryfikacja obrazu względem nośnika", raw_status, raw_reason)
    add("image_inspect", "Inspekcja obrazu")
    add("hash", "Sumy kontrolne")
    add("compare", "Porównanie obrazów")
    for fmt in ("json", "csv", "html", "txt"):
        add(f"report_{fmt}", f"Raport {fmt.upper()}")
    add("report_diff", "Zmiany między raportami")

    if win:
        add("unmount", "Bezpieczne odmontowanie", PARTIAL, "tylko woluminy z literą dysku (Shell Eject)")
        add("eject", "Przygotowanie do odłączenia", PARTIAL, "tylko woluminy z literą dysku (Shell Eject)")
    elif linux:
        tool = shutil.which("udisksctl") or shutil.which("umount")
        add("unmount", "Bezpieczne odmontowanie", OK if tool else NO, "" if tool else "brak umount/udisksctl")
        add("eject", "Przygotowanie do odłączenia",
            OK if (shutil.which("udisksctl") or shutil.which("eject")) else NO,
            "" if (shutil.which("udisksctl") or shutil.which("eject")) else "brak udisksctl/eject")
    else:
        add("unmount", "Bezpieczne odmontowanie")
        add("eject", "Przygotowanie do odłączenia")
    add("logs", "Logi")
    add("config", "Konfiguracja")
    add("diagnose", "Tryb diagnostyczny")
    add("dry_run", "Tryb symulacji (dry-run)")
    return F


def summary_lines(os_name: str = None) -> List[str]:
    out = []
    for f in features(os_name).values():
        extra = f"  – {f.reason}" if f.reason else ""
        out.append(f"{f.mark} {f.title:<42} {f.status}{extra}")
    return out

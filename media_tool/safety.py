"""Zabezpieczenia: wybór urządzenia, potwierdzenia, ponowna weryfikacja, ochrona dysku systemowego.

Zasady:
* operacje tylko do odczytu – pokazujemy dane urządzenia i pytamy o potwierdzenie (t/n);
* operacje zmieniające stan urządzenia (odmontowanie, odłączenie, w przyszłości zapis) –
  użytkownik musi wpisać pełną ścieżkę urządzenia, numer seryjny (jeśli jest) i słowo POTWIERDZAM;
* przed operacją dane urządzenia są odczytywane ponownie; jeśli się zmieniły – przerywamy.
"""
from __future__ import annotations

import enum
from typing import List, Optional

from . import logs
from .errors import DeviceChanged, DeviceNotFound, SafetyError
from .models import UNAVAILABLE, Device
from .ui import Console, UserAbort
from .util import human_size

CONFIRM_WORD = "POTWIERDZAM"


class Risk(enum.Enum):
    READ = "odczyt"                  # nic nie zmienia na nośniku
    FILE_WRITE = "zapis pliku"       # czyta nośnik, zapisuje tylko wskazany plik (np. obraz)
    DEVICE_STATE = "zmiana stanu"    # odmontowanie / odłączenie
    DEVICE_WRITE = "zapis na nośnik"  # zarezerwowane – program nie wykonuje takich operacji


def _v(x) -> str:
    return UNAVAILABLE if x is None or x == "" else str(x)


def describe_device(d: Device) -> List[str]:
    """Pełne podsumowanie pokazywane przed każdą operacją."""
    if d.mount_points:
        mounts = ", ".join(d.mount_points[:3]) + (f" (+{len(d.mount_points) - 3} kolejnych)" if len(d.mount_points) > 3 else "")
    else:
        mounts = "brak" if d.mount_points is not None else UNAVAILABLE
    sysflag = {True: "TAK – DYSK SYSTEMOWY", False: "nie", None: f"{UNAVAILABLE} (nie można potwierdzić)"}[d.is_system]
    lines = [
        f"  Ścieżka urządzenia : {d.path}",
        f"  Producent          : {_v(d.vendor)}",
        f"  Model              : {_v(d.model)}",
        f"  Numer seryjny      : {_v(d.serial)}",
        f"  Pojemność          : {human_size(d.size_detected_bytes)}",
        f"  Typ nośnika        : {_v(d.device_type)} ({_v(d.interface)})",
        f"  Zamontowany       : {_v({True: 'tak', False: 'nie', None: None}[d.mounted])}",
        f"  Dysk systemowy     : {sysflag}",
        f"  Punkty montowania  : {mounts}",
    ]
    if d.partitions:
        lines.append("  Partycje:")
        for p in d.partitions:
            lines.append(f"    - {_v(p.identifier)}  {human_size(p.size_bytes)}  {_v(p.fs_type)}  "
                         f"etykieta: {_v(p.volume_label)[:30]}  mount: "
                         f"{', '.join((p.mount_points or [])[:2]) or 'brak'}{' …' if len(p.mount_points or []) > 2 else ''}")
    else:
        lines.append("  Partycje           : brak / NIEDOSTĘPNE")
    return lines


def show_device(console: Console, d: Device) -> None:
    console.info("Wybrane urządzenie:")
    for ln in describe_device(d):
        console.info(ln)
    for note in d.notes:
        if "numer seryjny" in note.lower():
            console.warn(note)


def resolve_device(devices: List[Device], spec: str) -> Device:
    """Znajduje urządzenie po ścieżce/nazwie albo po `serial:`/`wwn:`/`uuid:`.

    Odmawia, gdy wybór jest niejednoznaczny. Podanie partycji (np. disk4s1) wskazuje cały dysk."""
    spec = spec.strip()
    low = spec.lower()
    matches: List[Device] = []
    for prefix, attr in (("serial:", "serial"), ("wwn:", "wwn"), ("uuid:", "uuid"), ("cardserial:", "card_serial")):
        if low.startswith(prefix):
            val = spec[len(prefix):].strip().lower()
            matches = [d for d in devices if (getattr(d, attr) or "").lower() == val]
            if prefix == "serial:":
                matches += [d for d in devices if (d.reader_serial or "").lower() == val and d not in matches]
            break
    else:
        norm = _norm(spec)
        matches = [d for d in devices if norm in {_norm(d.path), _norm(d.system_name or "")}]
        if not matches:  # może partycja
            matches = [d for d in devices if any(norm in {_norm(p.path or ""), _norm(p.identifier or "")}
                                                 for p in d.partitions)]
    if not matches:
        raise DeviceNotFound(f"Nie znaleziono urządzenia: {spec}. Użyj 'list', aby zobaczyć dostępne.")
    if len(matches) > 1:
        raise SafetyError(f"Wybór '{spec}' jest niejednoznaczny ({', '.join(m.path for m in matches)}). "
                          f"Podaj pełną ścieżkę.")
    return matches[0]


def _norm(s: str) -> str:
    return s.lower().replace("\\\\.\\", "").replace("/dev/", "")


def confirm_read(console: Console, d: Device, action: str, enabled: bool = True) -> None:
    """Pokazuje dane urządzenia i pyta o zgodę na operację tylko do odczytu."""
    show_device(console, d)
    if d.is_system:
        console.warn("To jest dysk systemowy. Operacja jest tylko do odczytu, ale może trwać długo.")
    if not enabled:
        return
    if console.assume_yes:
        logs.audit("confirm_read", action=action, device=d.path, via="--yes")
        return
    if not console.interactive:
        raise SafetyError("Operacja wymaga potwierdzenia. Uruchom w terminalu albo dodaj --yes "
                          "(dotyczy tylko operacji do odczytu).")
    if not console.confirm(f"Wykonać '{action}' na tym urządzeniu?", default=False):
        raise UserAbort()


def confirm_typed(console: Console, d: Device, action: str) -> None:
    """Potwierdzenie operacji zmieniającej stan: ścieżka + numer seryjny + POTWIERDZAM."""
    show_device(console, d)
    if d.is_system:
        raise SafetyError("To jest dysk systemowy – operacja zablokowana.")
    if d.is_system is None:
        console.warn("Nie można ustalić, czy to dysk systemowy (NIEDOSTĘPNE). Sprawdź to samodzielnie.")
    if not console.interactive:
        raise SafetyError("Ta operacja wymaga interaktywnego potwierdzenia w terminalu "
                          "(--yes nie działa dla operacji zmieniających stan).")
    console.warn(f"Operacja '{action}' zmieni stan urządzenia.")
    typed_path = console.ask(f"Wpisz pełną ścieżkę urządzenia ({d.path})")
    if typed_path != d.path:
        logs.audit("confirm_failed", action=action, device=d.path, reason="path")
        raise SafetyError("Wpisana ścieżka nie zgadza się – przerwano.")
    if d.serial:
        typed_sn = console.ask("Wpisz numer seryjny urządzenia")
        if typed_sn != d.serial:
            logs.audit("confirm_failed", action=action, device=d.path, reason="serial")
            raise SafetyError("Numer seryjny nie zgadza się – przerwano.")
    else:
        console.warn("Numer seryjny jest NIEDOSTĘPNY – potwierdzenie tylko ścieżką i słowem.")
    word = console.ask(f"Wpisz {CONFIRM_WORD}, aby kontynuować")
    if word != CONFIRM_WORD:
        logs.audit("confirm_failed", action=action, device=d.path, reason="word")
        raise SafetyError("Brak potwierdzenia – przerwano.")
    logs.audit("confirmed", action=action, device=d.path, fingerprint=d.state_fingerprint())


def revalidate(backend, d: Device, strict: bool = True) -> Device:
    """Ponownie odczytuje urządzenie i porównuje z tym, które potwierdził użytkownik."""
    fresh = next((x for x in backend.list_devices(include_virtual=True) if x.path == d.path), None)
    if fresh is None:
        raise DeviceChanged(f"Urządzenie {d.path} zniknęło po potwierdzeniu – przerwano.")
    a, b = (d.state(), fresh.state()) if strict else (d.identity(), fresh.identity())
    if a != b:
        diff = [k for k in a if a[k] != b.get(k)]
        logs.audit("device_changed", device=d.path, fields=diff)
        raise DeviceChanged(f"Dane urządzenia {d.path} zmieniły się po potwierdzeniu "
                            f"({', '.join(diff)}). Wykonaj ponownie wybór i potwierdzenie.")
    return fresh

"""Interaktywne menu (tryb krok po kroku)."""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

from . import capabilities, commands, config, logs
from .backends import BackendError
from .capabilities import NO, PARTIAL
from .commands import Context
from .errors import MediaToolError
from .scanning import parse_size
from .ui import UserAbort
from .util import human_size


def choose_device(ctx: Context) -> str:
    """Pokazuje listę i zwraca ścieżkę wybranego urządzenia (pełne dane pojawią się przed operacją)."""
    devs = ctx.devices(include_virtual=False, refresh=True)
    if not devs:
        raise MediaToolError("Nie znaleziono nośników.")
    labels = [f"{d.path:<14} {d.short_name()[:34]:<34} {human_size(d.size_detected_bytes):>10}  "
              f"{d.device_type or 'NIEDOSTĘPNE'}  nr seryjny: {d.serial or d.card_serial or d.reader_serial or 'NIEDOSTĘPNE'}"
              + ("  [SYSTEM]" if d.is_system else "") for d in devs]
    return devs[ctx.console.choose("Wybierz urządzenie:", labels)].path


def _ask_path(ctx: Context, prompt: str, default: str = "") -> str:
    p = ctx.console.ask(prompt, default)
    if not p:
        raise MediaToolError("Nie podano ścieżki.")
    return os.path.expanduser(p)


def _dev(fn: Callable) -> Callable:
    return lambda ctx: fn(ctx, choose_device(ctx))


def _benchmark(ctx):
    return commands.cmd_benchmark(ctx, choose_device(ctx))


def _integrity(ctx):
    spec = choose_device(ctx)
    raw = ctx.console.ask("Własne obszary START:DŁUGOŚĆ oddzielone przecinkami (Enter = domyślne)", "")
    ranges = [r.strip() for r in raw.split(",") if r.strip()] or None
    return commands.cmd_integrity(ctx, spec, ranges)


def _image(ctx):
    sub = ctx.console.choose("Obraz nośnika:", ["Utwórz obraz", "Wznów przerwane tworzenie", "Zbadaj plik obrazu"])
    if sub == 2:
        return commands.cmd_image_inspect(ctx, _ask_path(ctx, "Plik obrazu"),
                                          ctx.console.confirm("Obliczyć też sumę kontrolną?", False))
    spec = choose_device(ctx)
    out = _ask_path(ctx, "Plik wyjściowy obrazu (np. ~/backup.img)")
    over = sub == 0 and os.path.exists(out) and ctx.console.confirm(f"{out} istnieje – nadpisać?", False)
    return commands.cmd_image_create(ctx, spec, out, overwrite=over, resume=sub == 1)


def _verify(ctx):
    spec = choose_device(ctx)
    return commands.cmd_image_verify(ctx, spec, _ask_path(ctx, "Plik obrazu do porównania"))


def _hash(ctx):
    return commands.cmd_hash(ctx, _ask_path(ctx, "Plik / obraz"), ctx.console.ask("Algorytm", ctx.cfg["hash_algorithm"]))


def _compare(ctx):
    return commands.cmd_compare(ctx, _ask_path(ctx, "Obraz 1"), _ask_path(ctx, "Obraz 2"))


def _report(fmt: str):
    def run(ctx):
        scope = ctx.console.choose("Zakres raportu:", ["Wszystkie nośniki", "Jedno urządzenie"], default=0)
        spec = choose_device(ctx) if scope == 1 else None
        out = ctx.console.ask("Plik raportu", commands.default_report_path(ctx, fmt))
        redact = ctx.console.confirm("Zamaskować numery seryjne (do udostępniania)?", ctx.cfg["redact_serials"])
        return commands.cmd_report(ctx, fmt, os.path.expanduser(out), spec, redact=redact)
    return run


def _report_diff(ctx):
    return commands.cmd_report_diff(ctx, _ask_path(ctx, "Starszy raport JSON"), _ask_path(ctx, "Nowszy raport JSON"))


def _scan(ctx):
    spec = choose_device(ctx)
    start = ctx.console.ask("Początek zakresu (Enter = 0)", "0")
    length = ctx.console.ask("Długość (Enter = do końca)", "")
    return commands.cmd_scan(ctx, spec, parse_size(start), parse_size(length) if length else None)


def _logs(ctx):
    n = ctx.console.ask("Ile ostatnich linii", "50")
    return commands.cmd_logs(ctx, int(n) if n.isdigit() else 50)


def _config(ctx):
    sub = ctx.console.choose("Konfiguracja:", ["Pokaż", "Zmień wartość", "Przywróć domyślne", "Pokaż ścieżkę pliku"])
    if sub == 0:
        return commands.cmd_config(ctx, "show")
    if sub == 1:
        keys = list(config.SCHEMA)
        k = keys[ctx.console.choose("Który parametr?", [f"{k} – {config.SCHEMA[k][1]}" for k in keys])]
        return commands.cmd_config(ctx, "set", k, ctx.console.ask(f"Nowa wartość dla {k} (obecnie {ctx.cfg[k]})"))
    if sub == 2:
        if ctx.console.confirm("Przywrócić wszystkie ustawienia domyślne?", False):
            return commands.cmd_config(ctx, "reset")
        return None
    return commands.cmd_config(ctx, "path")


def _dryrun(ctx):
    ctx.dry_run = not ctx.dry_run
    ctx.console.line(f"Tryb symulacji: {'WŁĄCZONY – operacje tylko pokażą plan' if ctx.dry_run else 'wyłączony'}")


# (numer, klucz funkcji, tytuł, obsługa)
ITEMS: List[Tuple[str, str, Callable]] = [
    ("list", "Szybkie wykrywanie nośników", lambda c: commands.cmd_list(c)),
    ("list", "Pełna lista nośników", lambda c: commands.cmd_list(c, full=True)),
    ("inspect", "Szczegółowa identyfikacja urządzenia", _dev(commands.cmd_inspect)),
    ("serial", "Numery seryjne", _dev(commands.cmd_serial)),
    ("ids", "Identyfikatory sprzętowe (VID/PID/WWN/EUI-64)", _dev(commands.cmd_ids)),
    ("uuids", "UUID i identyfikatory systemu plików", _dev(commands.cmd_uuids)),
    ("partitions", "Partycje i woluminy", _dev(commands.cmd_partitions)),
    ("mounts", "Punkty montowania", _dev(commands.cmd_mount_status)),
    ("mounted", "Czy nośnik jest zamontowany", _dev(commands.cmd_mount_status)),
    ("system", "Czy nośnik jest systemowy", _dev(commands.cmd_system_status)),
    ("health", "SMART / stan zdrowia", _dev(commands.cmd_health)),
    ("benchmark", "Test prędkości odczytu", _benchmark),
    ("integrity", "Test integralności wybranych obszarów", _integrity),
    ("image_create", "Obraz nośnika (utwórz / wznów / zbadaj)", _image),
    ("image_verify", "Weryfikacja obrazu względem nośnika", _verify),
    ("hash", "Suma kontrolna pliku lub obrazu", _hash),
    ("compare", "Porównanie dwóch obrazów", _compare),
    ("report_json", "Raport JSON", _report("json")),
    ("report_csv", "Raport CSV", _report("csv")),
    ("report_html", "Raport HTML", _report("html")),
    ("report_txt", "Raport tekstowy", _report("txt")),
    ("report_diff", "Zmiany między dwoma raportami", _report_diff),
    ("space", "Wolne miejsce", _dev(commands.cmd_space)),
    ("errors", "Błędy odczytu", _dev(commands.cmd_errors)),
    ("scan", "Skanowanie nośnika (tylko odczyt)", _scan),
    ("unmount", "Bezpieczne odmontowanie", _dev(commands.cmd_unmount)),
    ("eject", "Przygotowanie do bezpiecznego odłączenia", _dev(commands.cmd_eject)),
    ("logs", "Logi", _logs),
    ("config", "Konfiguracja", _config),
    ("diagnose", "Tryb diagnostyczny", lambda c: commands.cmd_diagnose(c)),
    ("dry_run", "Tryb symulacji (dry-run) – włącz/wyłącz", _dryrun),
]


def run(ctx: Context) -> int:
    con = ctx.console
    feats = capabilities.features()
    logs.audit("menu_start")
    while True:
        con.line("\n=== media-tool – menu ===" + ("   [TRYB SYMULACJI]" if ctx.dry_run else ""))
        con.line(f"System: {ctx.backend.name}   Administrator: {'tak' if commands.is_admin() else 'nie'}   "
                 f"(✔ dostępne  ◐ częściowo  ✖ niedostępne)")
        for i, (key, title, _) in enumerate(ITEMS, 1):
            f = feats.get(key)
            con.line(f" {i:>2}) {f.mark if f else ' '} {title}")
        con.line(f" {len(ITEMS) + 1:>2})   Wyjście")
        try:
            raw = con.ask("Wybór")
        except UserAbort:
            return 0
        if not raw.isdigit() or not 1 <= int(raw) <= len(ITEMS) + 1:
            con.error(f"Podaj numer 1-{len(ITEMS) + 1}.")
            continue
        n = int(raw)
        if n == len(ITEMS) + 1:
            return 0
        key, title, handler = ITEMS[n - 1]
        f = feats.get(key)
        if f and f.status == NO:
            con.error(f"Funkcja niedostępna na tym systemie: {f.reason}")
            continue
        if f and f.status == PARTIAL and f.reason:
            con.warn(f"Funkcja częściowo dostępna: {f.reason}")
        try:
            handler(ctx)
        except (UserAbort, KeyboardInterrupt):
            con.error("Przerwano – wracam do menu.")
        except (MediaToolError, BackendError) as e:
            con.error(str(e))
        except ValueError as e:
            con.error(str(e))

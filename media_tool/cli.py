"""Interfejs wiersza poleceń (argparse)."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__, commands, config, logs
from .backends import Backend, BackendError, get_backend
from .errors import MediaToolError
from .scanning import parse_size
from .ui import Console, UserAbort

EXIT_PROBLEM = 3  # wykryto różnice / błędy (np. weryfikacja nie przeszła)


class UnsupportedBackend(Backend):
    name = "nieobsługiwany system"

    def __init__(self, reason: str):
        self.reason = reason

    def list_devices(self, include_virtual: bool = False):
        raise BackendError(self.reason)

    def tool_status(self):
        return {}


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="wynik w formacie JSON na stdout")
    p.add_argument("--dry-run", action="store_true", help="tylko pokaż, co zostałoby wykonane")
    p.add_argument("--yes", action="store_true",
                   help="pomiń pytanie t/n przy operacjach tylko do odczytu (nie dotyczy zmian stanu)")
    p.add_argument("-v", "--verbose", action="store_true", help="loguj także na stderr")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="media-tool", parents=[common], argument_default=argparse.SUPPRESS,
        description="Identyfikacja, diagnostyka, raportowanie i obrazowanie nośników danych. "
                    "Domyślnie wszystkie operacje są tylko do odczytu.")
    ap.add_argument("--version", action="version", version=f"media-tool {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="POLECENIE")

    def add(name, help_, **kw):
        return sub.add_parser(name, help=help_, parents=[common], argument_default=argparse.SUPPRESS, **kw)

    p = add("list", "lista nośników")
    p.add_argument("--full", action="store_true", help="pełne dane każdego urządzenia")
    p.add_argument("--external", action="store_true", help="tylko wymienne/zewnętrzne")
    p.add_argument("--include-virtual", action="store_true", help="także obrazy dysków i urządzenia wirtualne")
    for name, help_ in (("inspect", "szczegółowa identyfikacja urządzenia"), ("serial", "numery seryjne"),
                        ("ids", "identyfikatory sprzętowe (VID/PID/WWN/EUI-64)"),
                        ("uuids", "UUID, PARTUUID, identyfikatory systemu plików"),
                        ("partitions", "partycje i woluminy"), ("mount-status", "punkty montowania i status"),
                        ("system-status", "czy to dysk systemowy"), ("space", "wolne miejsce"),
                        ("health", "SMART / stan zdrowia"), ("errors", "błędy odczytu (SMART + próbki)"),
                        ("unmount", "bezpiecznie odmontuj (wymaga potwierdzenia)"),
                        ("eject", "odmontuj i przygotuj do odłączenia (wymaga potwierdzenia)")):
        add(name, help_).add_argument("device", metavar="DEVICE")

    p = add("benchmark", "test prędkości odczytu (tylko odczyt)")
    p.add_argument("device")
    p.add_argument("--size-mib", type=int, help="ilość danych do odczytu (MiB)")
    p = add("integrity", "test integralności wybranych obszarów (tylko odczyt)")
    p.add_argument("device")
    p.add_argument("--range", action="append", metavar="START:DŁUGOŚĆ", help="własny obszar (np. 0:1M); można powtarzać")
    p.add_argument("--samples", type=int, default=8, help="liczba losowych obszarów (domyślnie 8)")
    p.add_argument("--passes", type=int, default=2, help="ile razy czytać każdy obszar (domyślnie 2)")
    p.add_argument("--seed", type=int, help="ziarno losowania obszarów (powtarzalność)")
    p = add("scan", "skanowanie nośnika tylko do odczytu")
    p.add_argument("device")
    p.add_argument("--start", default="0", help="początek zakresu (np. 0, 1G)")
    p.add_argument("--length", help="długość zakresu (domyślnie do końca)")

    p = add("report", "raport JSON/CSV/HTML/TXT")
    p.add_argument("--format", choices=["json", "csv", "html", "txt"], default="json")
    p.add_argument("--output", "-o", help="plik wyjściowy")
    p.add_argument("--device", help="tylko to urządzenie (domyślnie wszystkie)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--redact", dest="redact", action="store_true", default=None, help="zamaskuj numery seryjne")
    p.add_argument("--no-redact", dest="redact", action="store_false", help="nie maskuj numerów seryjnych")
    p.add_argument("--no-health", action="store_true", help="pomiń odczyt SMART")
    p = add("report-diff", "zmiany między dwoma raportami JSON")
    p.add_argument("old")
    p.add_argument("new")

    p = add("hash", "suma kontrolna pliku lub obrazu")
    p.add_argument("file")
    p.add_argument("--algo", choices=["md5", "sha1", "sha256", "sha512", "blake2b"])
    p = add("compare", "porównaj dwa obrazy")
    p.add_argument("image1")
    p.add_argument("image2")
    p.add_argument("--algo", choices=["md5", "sha1", "sha256", "sha512", "blake2b"])

    img = add("image", "obrazy nośników: create / resume / verify / inspect")
    isub = img.add_subparsers(dest="image_command", metavar="AKCJA", required=True)
    for name in ("create", "resume"):
        q = isub.add_parser(name, parents=[common], argument_default=argparse.SUPPRESS,
                            help="utwórz obraz" if name == "create" else "wznów przerwane tworzenie obrazu")
        q.add_argument("device")
        q.add_argument("--output", "-o", required=True)
        q.add_argument("--algo", choices=["md5", "sha1", "sha256", "sha512", "blake2b"])
        if name == "create":
            q.add_argument("--overwrite", action="store_true", help="nadpisz istniejący plik obrazu")
    q = isub.add_parser("verify", parents=[common], argument_default=argparse.SUPPRESS, help="porównaj obraz z nośnikiem")
    q.add_argument("device")
    q.add_argument("--image", required=True)
    q = isub.add_parser("inspect", parents=[common], argument_default=argparse.SUPPRESS, help="zbadaj plik obrazu")
    q.add_argument("image")
    q.add_argument("--hash", action="store_true", help="oblicz i porównaj sumę kontrolną")

    p = add("dry-run", "uruchom dowolne polecenie w trybie symulacji")
    p.add_argument("rest", nargs=argparse.REMAINDER, metavar="POLECENIE ...")
    p = add("config", "pokaż/zmień konfigurację")
    p.add_argument("action", nargs="?", default="show", choices=["show", "get", "set", "reset", "path"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p = add("logs", "pokaż logi")
    p.add_argument("--lines", "-n", type=int, default=50)
    p.add_argument("--path", action="store_true", help="tylko ścieżka pliku logu")
    add("features", "dostępność funkcji na tym systemie")
    add("diagnose", "tryb diagnostyczny")
    add("menu", "interaktywne menu")
    return ap


def _flags(args) -> dict:
    return {"json": getattr(args, "json", False), "dry_run": getattr(args, "dry_run", False),
            "yes": getattr(args, "yes", False), "verbose": getattr(args, "verbose", False)}


def dispatch(ctx: commands.Context, a: argparse.Namespace) -> Optional[dict]:
    c = a.command
    g = lambda k, d=None: getattr(a, k, d)
    simple = {"inspect": commands.cmd_inspect, "serial": commands.cmd_serial, "ids": commands.cmd_ids,
              "uuids": commands.cmd_uuids, "partitions": commands.cmd_partitions,
              "mount-status": commands.cmd_mount_status, "system-status": commands.cmd_system_status,
              "space": commands.cmd_space, "health": commands.cmd_health, "errors": commands.cmd_errors,
              "unmount": commands.cmd_unmount, "eject": commands.cmd_eject}
    if c in simple:
        return simple[c](ctx, a.device)
    if c == "list":
        return commands.cmd_list(ctx, g("full", False), g("external", False), g("include_virtual", False))
    if c == "benchmark":
        return commands.cmd_benchmark(ctx, a.device, g("size_mib"))
    if c == "integrity":
        return commands.cmd_integrity(ctx, a.device, g("range"), g("samples", 8), g("passes", 2), g("seed"))
    if c == "scan":
        return commands.cmd_scan(ctx, a.device, parse_size(g("start", "0")),
                                 parse_size(a.length) if g("length") else None)
    if c == "report":
        return commands.cmd_report(ctx, g("format", "json"), g("output"), g("device"), g("overwrite", False),
                                   g("redact"), health=not g("no_health", False))
    if c == "report-diff":
        return commands.cmd_report_diff(ctx, a.old, a.new)
    if c == "hash":
        return commands.cmd_hash(ctx, a.file, g("algo"))
    if c == "compare":
        return commands.cmd_compare(ctx, a.image1, a.image2, g("algo"))
    if c == "image":
        ic = a.image_command
        if ic in ("create", "resume"):
            return commands.cmd_image_create(ctx, a.device, a.output, g("overwrite", False), ic == "resume", g("algo"))
        if ic == "verify":
            return commands.cmd_image_verify(ctx, a.device, a.image)
        return commands.cmd_image_inspect(ctx, a.image, g("hash", False))
    if c == "config":
        return commands.cmd_config(ctx, a.action, g("key"), g("value"))
    if c == "logs":
        return commands.cmd_logs(ctx, g("lines", 50), g("path", False))
    if c == "features":
        return commands.cmd_features(ctx)
    if c == "diagnose":
        return commands.cmd_diagnose(ctx)
    raise MediaToolError(f"Nieznane polecenie: {c}")


def exit_code(a: argparse.Namespace, res: Optional[dict]) -> int:
    if not isinstance(res, dict):
        return 0
    c = a.command
    bad = (c == "image" and a.image_command == "verify" and res.get("match") is False) or \
          (c == "compare" and res.get("identical") is False) or \
          (c == "scan" and res.get("clean") is False) or \
          (c == "integrity" and res.get("passed") is False) or \
          (c == "report-diff" and res.get("identical") is False)
    return EXIT_PROBLEM if bad else 0


def main(argv: Optional[List[str]] = None, console: Optional[Console] = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if getattr(a, "command", None) == "dry-run":
        rest = [x for x in a.rest if x != "--"]
        if not rest:
            ap.error("dry-run: podaj polecenie, np. dry-run image create DEVICE --output x.img")
        flags = _flags(a)
        a = ap.parse_args(rest)
        for k, v in flags.items():
            if v and not getattr(a, k, False):
                setattr(a, k, v)
        a.dry_run = True
    f = _flags(a)
    cfg = config.load()
    logs.setup(cfg["log_level"], verbose=f["verbose"])
    console = console or Console(assume_yes=f["yes"])
    console.assume_yes = f["yes"] or console.assume_yes
    try:
        backend = get_backend()
    except BackendError as e:
        backend = UnsupportedBackend(str(e))
    ctx = commands.Context(backend=backend, console=console, cfg=cfg, dry_run=f["dry_run"], as_json=f["json"])
    logs.audit("start", argv=argv if argv is not None else sys.argv[1:], version=__version__)

    try:
        if getattr(a, "command", None) in (None, "menu"):
            if not console.interactive and a.command is None:
                ap.print_help()
                return 0
            from . import menu
            return menu.run(ctx)
        res = dispatch(ctx, a)
        return exit_code(a, res)
    except (UserAbort, KeyboardInterrupt):
        console.error("Przerwano.")
        logs.audit("aborted")
        return 130
    except (MediaToolError, BackendError) as e:
        console.error(str(e))
        logs.audit("error", error=str(e))
        return 1
    except BrokenPipeError:
        return 0

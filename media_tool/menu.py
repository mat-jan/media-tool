"""Interaktywne menu: grupy tematyczne, kolory, karty urządzeń przy wyborze."""
from __future__ import annotations

import datetime
import os
import shutil
from typing import Callable, List, Optional, Tuple

from . import __version__, admin, backup as backup_mod, capabilities, commands, config, logs, picker, ui
from .backends import BackendError
from .capabilities import NO, PARTIAL
from .commands import Context
from .errors import MediaToolError
from .scanning import parse_size
from .ui import UserAbort
from .util import human_size, is_admin


# ------------------------------------------------------------------ wybór urządzenia
def _badges(d) -> str:
    out = []
    if d.is_system:
        out.append(ui.red(ui.bold("SYSTEM")))
    if d.removable:
        out.append(ui.green("wymienny"))
    elif d.internal:
        out.append(ui.dim("wewnętrzny"))
    if d.mounted:
        out.append(ui.cyan("zamontowany"))
    return " ".join(out)


def choose_device(ctx: Context, title: str = "Wybierz urządzenie") -> str:
    """Pokazuje karty urządzeń i zwraca ścieżkę wybranego (pełne dane pojawią się przed operacją)."""
    ctx.console.info(ui.dim("Szukam nośników..."))
    devs = ctx.devices(include_virtual=False, refresh=True)
    if not devs:
        raise MediaToolError("Nie znaleziono nośników.")
    labels = []
    for d in devs:
        sn = d.serial or d.card_serial or d.reader_serial
        labels.append(
            f"{ui.bold(d.path)}  {d.short_name()[:36]}  {ui.cyan(human_size(d.size_detected_bytes))}\n"
            f"      {ui.dim((d.device_type or 'NIEDOSTĘPNE') + ' · ' + (d.interface or '?') + ' · nr seryjny: ' + (sn or 'NIEDOSTĘPNE'))}"
            f"  {_badges(d)}")
    return devs[ctx.console.choose(title, labels)].path


def _ask_path(ctx: Context, prompt: str, default: str = "") -> str:
    p = ctx.console.ask(prompt, default)
    if not p:
        raise MediaToolError("Nie podano ścieżki.")
    return os.path.expanduser(p)


def ask_folder(ctx: Context, prompt: str, default: str = "") -> str:
    """Wybór folderu: okno systemowe (gdy dostępne), domyślny albo wpisanie ręcznie."""
    con = ctx.console
    default = os.path.expanduser(default) if default else ""
    if picker.gui_available():
        options = ["Wybierz folder w oknie  (zalecane)"]
        if default:
            options.append(f"Użyj domyślnego: {default}")
        options.append("Wpisz ścieżkę ręcznie")
        choice = con.choose(prompt, options, default=0)
        if choice == 0:
            con.info(ui.dim("Otwieram okno wyboru folderu..."))
            try:
                picked = picker.pick_folder(prompt, default or os.path.expanduser("~"))
            except picker.PickerUnavailable as e:
                con.warn(f"Okno wyboru niedostępne ({e}) – wpisz ścieżkę.")
            else:
                if picked is None:
                    raise UserAbort()               # anulowano okno = rezygnacja z operacji
                con.info(f"Wybrano: {picked}")
                return picked
        elif default and choice == 1:
            return default
        return _ask_path(ctx, "Folder", default)
    return _ask_path(ctx, prompt, default)


def ask_file(ctx: Context, prompt: str, start: str = "") -> str:
    """Wybór istniejącego pliku: okno systemowe albo wpisanie ścieżki."""
    con = ctx.console
    if picker.gui_available():
        choice = con.choose(prompt, ["Wybierz plik w oknie  (zalecane)", "Wpisz ścieżkę ręcznie"], default=0)
        if choice == 0:
            con.info(ui.dim("Otwieram okno wyboru pliku..."))
            try:
                picked = picker.pick_file(prompt, start or os.path.expanduser("~"))
            except picker.PickerUnavailable as e:
                con.warn(f"Okno wyboru niedostępne ({e}) – wpisz ścieżkę.")
            else:
                if picked is None:
                    raise UserAbort()
                con.info(f"Wybrano: {picked}")
                return picked
    return _ask_path(ctx, prompt)


def ask_output_file(ctx: Context, prompt: str, default_name: str, default_dir: str = "") -> str:
    """Wybór miejsca zapisu pliku: folder (okno) + nazwa pliku (domyślna proponowana)."""
    folder = ask_folder(ctx, f"{prompt} – folder", default_dir or os.path.expanduser("~"))
    name = ctx.console.ask("Nazwa pliku", default_name)
    return os.path.join(folder, name or default_name)


def _ensure_admin(ctx: Context, why: str) -> bool:
    """True = można kontynuować (już administrator). Inaczej proponuje ponowne uruchomienie z sudo/UAC."""
    if is_admin():
        return True
    con = ctx.console
    if not admin.can_elevate():
        con.error("Ta funkcja wymaga uprawnień administratora, a sudo/UAC jest niedostępne – uruchom program jako administrator.")
        return False
    con.box("Potrzebne uprawnienia administratora",
            [why, "Program uruchomi się ponownie z uprawnieniami (zapyta o hasło) i wróci od razu do tej funkcji."],
            kind="warn", stream="out")
    if con.confirm("Uruchomić ponownie z uprawnieniami administratora?", True):
        try:
            admin.relaunch_as_admin(start_item=getattr(ctx, "current_item", None))
        except OSError as e:
            con.error(str(e))
    return False


def _dev(fn: Callable) -> Callable:
    return lambda ctx: fn(ctx, choose_device(ctx))


# ------------------------------------------------------------------ obsługa pozycji
def _benchmark(ctx):
    return commands.cmd_benchmark(ctx, choose_device(ctx))


def _integrity(ctx):
    spec = choose_device(ctx)
    raw = ctx.console.ask("Własne obszary START:DŁUGOŚĆ oddzielone przecinkami (Enter = domyślne)", "")
    return commands.cmd_integrity(ctx, spec, [r.strip() for r in raw.split(",") if r.strip()] or None)


def _scan(ctx):
    spec = choose_device(ctx)
    start = ctx.console.ask("Początek zakresu (Enter = 0)", "0")
    length = ctx.console.ask("Długość (Enter = do końca)", "")
    return commands.cmd_scan(ctx, spec, parse_size(start), parse_size(length) if length else None)


def _backup_dir(ctx) -> str:
    return os.path.expanduser(ctx.cfg["backup_dir"])


def _card_image_name(d) -> str:
    labels = sorted({p.volume_label for p in d.partitions if p.volume_label})
    who = backup_mod.sanitize_name(labels[0] if len(labels) == 1 else (d.model or d.name or "nosnik"))
    sn = backup_mod.sanitize_name((d.serial or d.card_serial or d.reader_serial or "")[:12], fallback="")
    return f"{who}{'-' + sn if sn else ''}-{datetime.datetime.now():%Y%m%d-%H%M%S}.img"


def _backup_card(ctx, spec: str):
    """Kopia CAŁEJ karty/nośnika: obraz 1:1 (wszystkie partycje, tablica partycji, nieprzydzielone miejsce)."""
    con = ctx.console
    dev = next((x for x in ctx.devices(include_virtual=True) if x.path == spec), None)
    if not _ensure_admin(ctx, "Kopia całej karty czyta surowe urządzenie (wszystkie partycje), co wymaga uprawnień administratora."):
        return None
    size = dev.size_detected_bytes if dev else None
    dest_dir = ask_folder(ctx, "Gdzie zapisać obraz całej karty (folder docelowy)", _backup_dir(ctx))
    output = os.path.join(dest_dir, con.ask("Nazwa pliku obrazu", _card_image_name(dev) if dev else "karta.img"))
    if size:
        probe = dest_dir
        while probe and not os.path.exists(probe):
            probe = os.path.dirname(probe)
        free = shutil.disk_usage(probe or ".").free
        con.info(f"Rozmiar karty: {human_size(size)}   wolne miejsce w celu: {human_size(free)}")
        if free < size + 64 * 1048576:
            raise MediaToolError(f"Za mało miejsca w folderze docelowym: potrzeba {human_size(size)}, wolne {human_size(free)}")
    if dev and dev.mounted and not dev.read_only and con.confirm(
            "Karta jest zamontowana. Odmontować ją przed kopią (zalecane – obraz będzie spójny)?", True):
        commands.cmd_unmount(ctx, spec)
    res = commands.cmd_image_create(ctx, spec, output)
    if not ctx.dry_run and res and con.confirm("Zweryfikować obraz względem karty (czyta kartę drugi raz, trwa tyle samo)?", True):
        con.assume_yes, was = True, con.assume_yes          # to samo urządzenie, zgoda na odczyt już udzielona
        try:
            commands.cmd_image_verify(ctx, spec, output)
        finally:
            con.assume_yes = was
    if not ctx.dry_run and res and con.confirm("Gotowe. Bezpiecznie wysunąć kartę teraz?", False):
        commands.cmd_eject(ctx, spec)
    return res


def _backup_run(ctx):
    con = ctx.console
    mode = con.choose("Co chcesz zabezpieczyć?", [
        "CAŁĄ KARTĘ / cały nośnik – obraz 1:1 (wszystkie partycje; można przywrócić na kartę)  [administrator]",
        "Same pliki (zdjęcia, filmy, dokumenty) – ze WSZYSTKICH woluminów karty",
        "Zwykły folder z komputera"], default=0)
    if mode == 0:
        spec = choose_device(ctx, "Którą kartę / który nośnik skopiować w całości?")
        return _backup_card(ctx, spec)
    spec = source = None
    if mode == 1:
        spec = choose_device(ctx, "Z którego nośnika skopiować pliki?")
    else:
        source = ask_folder(ctx, "Folder źródłowy", os.path.expanduser("~"))
    dest = ask_folder(ctx, "Gdzie zapisać kopię (folder docelowy)", _backup_dir(ctx))
    kinds = con.choose("Które pliki kopiować?", ["Wszystkie", "Tylko zdjęcia i filmy", "Własny wybór kategorii"], default=0)
    only = {0: None, 1: "zdjęcia,filmy"}.get(kinds)
    if kinds == 2:
        only = con.ask(f"Kategorie oddzielone przecinkami ({', '.join(sorted(set(backup_mod.CATEGORIES) | {'inne'}))})")
    verify = con.confirm("Sprawdzić każdą kopię odczytem zwrotnym (bezpieczniej, trochę wolniej)?", True)
    res = commands.cmd_backup_run(ctx, spec, dest, only=only, verify=verify, source_path=source,
                                  volume="all" if spec else None)
    if spec and not ctx.dry_run and not (res["failed"] or res["scan_errors"]):
        if con.confirm("Kopia gotowa. Bezpiecznie wysunąć nośnik teraz?", False):
            commands.cmd_eject(ctx, spec)
    return res


def _backup_verify(ctx):
    root = ask_folder(ctx, "Folder z kopiami", _backup_dir(ctx))
    info = backup_mod.list_backups(root)
    if info["kind"] == "root":
        if not info["sets"]:
            raise MediaToolError(f"W {root} nie ma kopii media-tool.")
        names = [f"{s['name']}  ({s['files']} plików, {human_size(s['bytes'])}, ostatnia: {s['last_run'] or '?'})" for s in info["sets"]]
        root = info["sets"][ctx.console.choose("Którą kopię sprawdzić?", names)]["path"]
    return commands.cmd_backup_verify(ctx, root)


def _backup_list(ctx):
    return commands.cmd_backup_list(ctx, ask_folder(ctx, "Folder z kopiami", _backup_dir(ctx)))


def _image(ctx):
    sub = ctx.console.choose("Obraz nośnika:", ["Utwórz obraz", "Wznów przerwane tworzenie", "Zbadaj plik obrazu"])
    if sub == 2:
        return commands.cmd_image_inspect(ctx, ask_file(ctx, "Plik obrazu do zbadania"),
                                          ctx.console.confirm("Obliczyć też sumę kontrolną?", False))
    if not _ensure_admin(ctx, "Obraz nośnika czyta surowe urządzenie, co wymaga uprawnień administratora."):
        return None
    spec = choose_device(ctx)
    dev = next((x for x in ctx.devices(include_virtual=True) if x.path == spec), None)
    if sub == 1:
        out = ask_file(ctx, "Plik obrazu do wznowienia")
        return commands.cmd_image_create(ctx, spec, out, resume=True)
    out = ask_output_file(ctx, "Gdzie zapisać obraz", _card_image_name(dev) if dev else "obraz.img", _backup_dir(ctx))
    over = os.path.exists(out) and ctx.console.confirm(f"{out} istnieje – nadpisać?", False)
    return commands.cmd_image_create(ctx, spec, out, overwrite=over)


def _verify(ctx):
    if not _ensure_admin(ctx, "Porównanie obrazu z nośnikiem czyta surowe urządzenie, co wymaga uprawnień administratora."):
        return None
    spec = choose_device(ctx)
    return commands.cmd_image_verify(ctx, spec, ask_file(ctx, "Plik obrazu do porównania"))


def _restore(ctx):
    ctx.console.box("Przywracanie obrazu = ZAPIS na nośnik",
                    ["Zapisuje obraz na wybrany nośnik i NADPISUJE wszystkie jego dane.",
                     "Dozwolone tylko na nośnikach zewnętrznych/wymiennych (nie na dysku systemowym).",
                     "Przed zapisem trzeba będzie wpisać ścieżkę, numer seryjny i słowo NADPISZ."], kind="bad", stream="out")
    if not _ensure_admin(ctx, "Zapis na surowe urządzenie wymaga uprawnień administratora."):
        return None
    image = ask_file(ctx, "Plik obrazu do przywrócenia")
    spec = choose_device(ctx, "Na który nośnik zapisać obraz?")
    return commands.cmd_image_restore(ctx, spec, image)


def _hash(ctx):
    return commands.cmd_hash(ctx, ask_file(ctx, "Plik / obraz"), ctx.console.ask("Algorytm", ctx.cfg["hash_algorithm"]))


def _compare(ctx):
    return commands.cmd_compare(ctx, ask_file(ctx, "Obraz 1"), ask_file(ctx, "Obraz 2"))


def _report(fmt: str):
    def run(ctx):
        scope = ctx.console.choose("Zakres raportu:", ["Wszystkie nośniki", "Jedno urządzenie"], default=0)
        spec = choose_device(ctx) if scope == 1 else None
        default = commands.default_report_path(ctx, fmt)
        out = ask_output_file(ctx, "Gdzie zapisać raport", os.path.basename(default), os.path.dirname(os.path.abspath(default)))
        redact = ctx.console.confirm("Zamaskować numery seryjne (do udostępniania)?", ctx.cfg["redact_serials"])
        return commands.cmd_report(ctx, fmt, out, spec, redact=redact)
    return run


def _report_diff(ctx):
    return commands.cmd_report_diff(ctx, ask_file(ctx, "Starszy raport JSON"), ask_file(ctx, "Nowszy raport JSON"))


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
    ctx.console.line(f"Tryb symulacji: {ui.yellow('WŁĄCZONY – operacje tylko pokażą plan') if ctx.dry_run else 'wyłączony'}")


Item = Tuple[str, str, Callable]
# (nazwa grupy, [(klucz funkcji, tytuł, obsługa)])
GROUPS: List[Tuple[str, List[Item]]] = [
    ("NOŚNIKI I IDENTYFIKACJA", [
        ("list", "Szybkie wykrywanie nośników", lambda c: commands.cmd_list(c)),
        ("list", "Pełna lista nośników (wszystkie dane)", lambda c: commands.cmd_list(c, full=True)),
        ("inspect", "Szczegółowa identyfikacja urządzenia", _dev(commands.cmd_inspect)),
        ("serial", "Numery seryjne (urządzenie / karta SD / czytnik)", _dev(commands.cmd_serial)),
        ("ids", "Identyfikatory sprzętowe (VID/PID/WWN/EUI-64)", _dev(commands.cmd_ids)),
        ("uuids", "UUID i identyfikatory systemu plików", _dev(commands.cmd_uuids)),
        ("partitions", "Partycje i woluminy", _dev(commands.cmd_partitions)),
        ("mounts", "Punkty montowania", _dev(commands.cmd_mount_status)),
        ("mounted", "Czy nośnik jest zamontowany", _dev(commands.cmd_mount_status)),
        ("system", "Czy nośnik jest systemowy", _dev(commands.cmd_system_status)),
        ("space", "Wolne miejsce", _dev(commands.cmd_space)),
    ]),
    ("KOPIE ZAPASOWE", [
        ("backup", "Kopia plików z karty / dysku (przyrostowa, z weryfikacją)", _backup_run),
        ("backup_verify", "Sprawdź, czy kopia plików jest nienaruszona", _backup_verify),
        ("backup_list", "Lista kopii plików", _backup_list),
        ("image_create", "Obraz całego nośnika (utwórz / wznów / zbadaj)", _image),
        ("image_verify", "Porównaj obraz z nośnikiem", _verify),
        ("image_restore", "Przywróć obraz na nośnik  (ZAPIS – nadpisuje dane!)", _restore),
    ]),
    ("DIAGNOSTYKA (tylko odczyt)", [
        ("health", "SMART / stan zdrowia", _dev(commands.cmd_health)),
        ("benchmark", "Test prędkości odczytu", _benchmark),
        ("integrity", "Test integralności wybranych obszarów", _integrity),
        ("errors", "Błędy odczytu", _dev(commands.cmd_errors)),
        ("scan", "Skanowanie nośnika", _scan),
    ]),
    ("RAPORTY", [
        ("report_json", "Raport JSON", _report("json")),
        ("report_csv", "Raport CSV", _report("csv")),
        ("report_html", "Raport HTML", _report("html")),
        ("report_txt", "Raport tekstowy", _report("txt")),
        ("report_diff", "Zmiany między dwoma raportami", _report_diff),
    ]),
    ("NARZĘDZIA", [
        ("hash", "Suma kontrolna pliku lub obrazu", _hash),
        ("compare", "Porównanie dwóch obrazów", _compare),
        ("unmount", "Bezpieczne odmontowanie", _dev(commands.cmd_unmount)),
        ("eject", "Przygotowanie do bezpiecznego odłączenia", _dev(commands.cmd_eject)),
        ("logs", "Logi", _logs),
        ("config", "Konfiguracja", _config),
        ("diagnose", "Tryb diagnostyczny", lambda c: commands.cmd_diagnose(c)),
        ("dry_run", "Tryb symulacji (dry-run) – włącz/wyłącz", _dryrun),
    ]),
]
ITEMS: List[Item] = [it for _, items in GROUPS for it in items]


def render_menu(ctx: Context, feats) -> None:
    con = ctx.console
    con.banner(f"media-tool {__version__}", f"{ctx.backend.name} · administrator: {'tak' if commands.is_admin() else 'nie'}"
                                            + ("   [TRYB SYMULACJI]" if ctx.dry_run else ""))
    con.line(ui.dim(f"  {ui.green(ui.sym('ok'))} dostępne   {ui.yellow(ui.sym('partial'))} częściowo   {ui.red(ui.sym('bad'))} niedostępne"))
    n = 0
    for gname, items in GROUPS:
        con.line(f"\n {ui.heading(gname)}")
        for key, title, _ in items:
            n += 1
            f = feats.get(key)
            con.line(f"  {ui.cyan(f'{n:>2}')}  {f.mark if f else ' '}  {title}")
    con.line(f"\n  {ui.cyan(f'{len(ITEMS) + 1:>2}')}     Wyjście")


RAW_KEYS = {"benchmark", "integrity", "scan", "errors"}     # inne funkcje pytają o administratora same


def run(ctx: Context) -> int:
    con = ctx.console
    feats = capabilities.features()
    logs.audit("menu_start")
    pending = os.environ.pop(admin.START_ITEM_ENV, "")            # po restarcie z sudo: od razu wróć do wybranej funkcji
    while True:
        try:
            if pending.isdigit():
                raw, pending = pending, ""
            else:
                render_menu(ctx, feats)
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
        con.line(f"\n{ui.heading('▸ ' + title if not ui._ascii_mode() else '> ' + title)}")
        ctx.current_item = n
        try:
            if key in RAW_KEYS and not is_admin() and admin.can_elevate():
                _ensure_admin(ctx, "Pełny odczyt surowego urządzenia wymaga uprawnień administratora "
                                   "(bez nich działa tylko ograniczony tryb albo funkcja się nie uruchomi).")
            handler(ctx)
        except (UserAbort, KeyboardInterrupt):
            con.error("Przerwano – wracam do menu.")
        except (MediaToolError, BackendError) as e:
            con.error(str(e))
        except ValueError as e:
            con.error(str(e))

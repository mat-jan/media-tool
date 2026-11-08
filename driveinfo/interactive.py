"""Interactive terminal wizard: the user chooses devices, actions, fields,
format and destination. Nothing is written unless explicitly chosen."""
import csv
import datetime
import json
import sys

from . import collect, report, tests
from .collect import FIELDS


def ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        v = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raise KeyboardInterrupt
    return v or (default or "")


def yes(prompt, default=False):
    v = ask(f"{prompt} (t/n)", "t" if default else "n").lower()
    return v in ("t", "y", "tak", "yes")


def parse_selection(text, n):
    """'1,3-5' / 'a' (all) / 'n' (none) -> sorted list of 0-based indices."""
    text = text.strip().lower()
    if text in ("a", "all", "wszystko"):
        return list(range(n))
    if text in ("", "n", "none"):
        return []
    out = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            a, b = int(lo), int(hi or lo)
        except ValueError:
            raise ValueError(f"Nie rozumiem: '{part}'")
        if not (1 <= a <= b <= n):
            raise ValueError(f"Poza zakresem 1-{n}: '{part}'")
        out.update(range(a - 1, b))
    return sorted(out)


def choose(title, labels, multi=True, default=None):
    """Numbered menu. Returns list of chosen indices."""
    print(f"\n{title}")
    for i, lab in enumerate(labels, 1):
        print(f"  {i:>2}) {lab}")
    hint = "numery (np. 1,3-5), a = wszystkie, Enter = żadne" if multi else "numer"
    while True:
        raw = ask(f"Wybór – {hint}", default)
        try:
            sel = parse_selection(raw, len(labels))
        except ValueError as e:
            print(f"  ! {e}")
            continue
        if not multi and len(sel) != 1:
            print("  ! Wybierz dokładnie jedną pozycję.")
            continue
        return sel


def device_label(d):
    mount = d["mount_points"].split(";")[0] or "niezamontowany"
    return f"{d['device']:<12} {d['model'] or d['name']:<24} {d['size']:>10}  {d['bus']} {d['kind']}  [{mount}]"


def show_info(d):
    print(f"\n--- {d['device']} ---")
    for k in FIELDS:
        if d.get(k) not in ("", None):
            print(f"  {k:<16} {d[k]}")


def print_result(res):
    for k, v in res.items():
        if v not in ("", None):
            print(f"  {k}: {v}")


def progress(phase, frac, rate):
    extra = f"  {rate:6.1f} MB/s" if rate else ""
    print(f"\r  {phase:<10} {frac * 100:5.1f}%{extra}   ", end="", flush=True)


def pick_mount(d):
    mounts = [m for m in d["mount_points"].split(";") if m]
    if not mounts:
        print(f"  ! {d['device']} nie jest zamontowany – test niemożliwy.")
        return None
    mount = mounts[0] if len(mounts) == 1 else mounts[choose("Który wolumin testować?", mounts, multi=False)[0]]
    mount = ask("Folder do testu (Enter = zatwierdź)", mount)
    return mount


def run_tests(devs, want_speed, want_cap):
    results = {d["device"]: [] for d in devs}
    for d in devs:
        mount = pick_mount(d)
        if not mount:
            continue
        try:
            if want_speed:
                size = int(ask("Rozmiar pliku testu prędkości w MB", "256"))
                print(f"\nTest prędkości: {d['device']} ({mount}) – zapisze tymczasowy plik i go usunie.")
                if yes("Uruchomić?", True):
                    res = tests.speed_test(mount, size, progress)
                    print()
                    print_result(res)
                    results[d["device"]].append(res)
            if want_cap:
                lim = ask("Limit testu w MB (Enter = cała wolna przestrzeń, może długo trwać)", "")
                print(f"\nTest pojemności: {d['device']} ({mount}) – zapisze i zweryfikuje dane w folderze "
                      f"tymczasowym, potem go usunie.")
                if yes("Uruchomić?", False):
                    res = tests.capacity_test(mount, int(lim) if lim else None, progress)
                    print()
                    print_result(res)
                    results[d["device"]].append(res)
        except ValueError as e:
            print(f"\n  ! {e}")
        except tests.Cancelled:
            print("\n  Przerwano.")
        except KeyboardInterrupt:
            print("\n  Przerwano.")
    return results


def do_export(devs):
    idx = choose("Które pola wyeksportować?", FIELDS, default="a")
    fields = [FIELDS[i] for i in idx]
    if not fields:
        print("Nie wybrano pól.")
        return
    fmt = ["csv", "json", "ekran"][choose("Format / miejsce:", [
        "CSV (plik)", "JSON (plik)", "tylko wyświetl na ekranie (bez zapisu)"], multi=False)[0]]
    rows = [{k: d[k] for k in fields} for d in devs]
    if fmt == "ekran":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    default = f"drive_info_{datetime.datetime.now():%Y%m%d_%H%M%S}.{fmt}"
    path = ask("Zapisz jako (ścieżka pliku)", default)
    if not yes(f"Zapisać {len(rows)} urządzeń do '{path}'?", True):
        print("Nie zapisano.")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        if fmt == "json":
            json.dump(rows, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        else:
            w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_ALL)
            w.writeheader()
            w.writerows(rows)
    print(f"Zapisano: {path}")


def do_report(devs, results):
    for d in devs:
        default = f"drive_report_{d['device'].split('/')[-1]}.html"
        path = ask(f"Raport dla {d['device']} – plik .html lub .pdf (Enter = pomiń)", "")
        if not path:
            continue
        try:
            report.save(d, results.get(d["device"], []), path)
            print(f"Zapisano: {path}")
        except Exception as e:
            print(f"  ! {e}")


ACTIONS = [
    "Pokaż informacje na ekranie",
    "Eksportuj dane do pliku (CSV / JSON) – z wyborem pól",
    "Test prędkości (zapisuje tymczasowy plik na nośniku)",
    "Test podróbek / pojemności (zapisuje i weryfikuje dane na nośniku)",
    "Raport HTML / PDF",
]


def run():
    print("Drive Info – kreator. Nic nie jest zapisywane bez Twojej zgody. Ctrl+C = wyjście.")
    while True:
        try:
            devices = collect.collect()
            if not devices:
                print("Nie znaleziono urządzeń.")
                if yes("Wyszukać ponownie?", True):
                    continue
                return
            sel = choose("1. Wybierz urządzenia:", [device_label(d) for d in devices])
            if not sel:
                if yes("Nic nie wybrano. Zakończyć?", True):
                    return
                continue
            devs = [devices[i] for i in sel]
            acts = choose("2. Co chcesz zrobić? (można kilka)", ACTIONS)
            if not acts:
                print("Nic nie wybrano.")
                continue
            results = {}
            if 0 in acts:
                for d in devs:
                    show_info(d)
            if 2 in acts or 3 in acts:
                results = run_tests(devs, 2 in acts, 3 in acts)
            if 1 in acts:
                do_export(devs)
            if 4 in acts:
                do_report(devs, results)
            if not yes("\nWrócić do menu głównego?", True):
                return
        except KeyboardInterrupt:
            print("\nDo widzenia.")
            return

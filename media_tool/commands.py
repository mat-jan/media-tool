"""Implementacje poleceń – wspólne dla CLI i menu interaktywnego.

Każde polecenie zwraca słownik z wynikiem; tekstowa prezentacja jest wypisywana na konsoli,
a w trybie --json wypisywany jest sam JSON (na stdout), komunikaty pomocnicze idą na stderr.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import benchmark as bench
from . import capabilities, config, imaging, logs, reports, safety, scanning
from .backends import Backend, BackendError
from .errors import Cancelled, MediaToolError, RawAccessError, SafetyError
from .models import UNAVAILABLE, Device
from .rawio import can_read, file_size
from .safety import Risk
from .ui import Console, format_table
from .util import human_size, is_admin

AREA_DEFAULT = 1048576


@dataclass
class Context:
    backend: Backend
    console: Console
    cfg: Dict[str, Any] = field(default_factory=config.load)
    dry_run: bool = False
    as_json: bool = False
    _devices: Optional[List[Device]] = None

    # ---- urządzenia
    def devices(self, include_virtual: bool = False, refresh: bool = False) -> List[Device]:
        if self._devices is None or refresh or include_virtual:
            self._devices = self.backend.list_devices(include_virtual=include_virtual)
        return self._devices

    def progress(self, label: str, frac: float, extra: str = "") -> None:
        if not self.as_json:
            self.console.progress(label, frac, extra)

    def pick(self, spec: str, risk: Optional[Risk] = None, action: str = "") -> Device:
        """Wybiera urządzenie. Dla operacji na nośniku wymaga potwierdzenia; dla zmiany stanu – pełnego."""
        d = safety.resolve_device(self.devices(include_virtual=True, refresh=True), spec)
        if risk is None:
            return d
        logs.audit("select", action=action, device=d.path, risk=risk.value, fingerprint=d.identity_fingerprint(),
                   dry_run=self.dry_run)
        if self.dry_run:
            # symulacja nic nie wykonuje – pokazujemy urządzenie i plan, bez potwierdzeń
            safety.show_device(self.console, d)
            if risk is Risk.DEVICE_STATE and d.is_system:
                self.console.warn("To dysk systemowy – prawdziwa operacja zostałaby zablokowana.")
            return d
        if risk is Risk.DEVICE_STATE:
            safety.confirm_typed(self.console, d, action)
        else:
            safety.confirm_read(self.console, d, action, enabled=self.cfg.get("confirm_read_ops", True))
        return safety.revalidate(self.backend, d, strict=risk is Risk.DEVICE_STATE)

    # ---- wyjście
    def out(self, data: Dict[str, Any], lines: List[str]) -> Dict[str, Any]:
        if self.as_json:
            self.console.line(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        else:
            for ln in lines:
                self.console.line(ln)
        return data


def _v(x: Any) -> str:
    if x is None or x == "":
        return UNAVAILABLE
    if isinstance(x, bool):
        return "tak" if x else "nie"
    if isinstance(x, list):
        return ", ".join(map(str, x)) if x else "brak"
    return str(x)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _size(n: Optional[int]) -> str:
    return UNAVAILABLE if n is None else f"{human_size(n)} ({n} B)"


def _pairs(pairs: List[tuple], indent: str = "  ") -> List[str]:
    w = max(len(k) for k, _ in pairs)
    return [f"{indent}{k:<{w}} : {v}" for k, v in pairs]


# ===================================================================== lista / identyfikacja
def cmd_list(ctx: Context, full: bool = False, external: bool = False, include_virtual: bool = False) -> Dict:
    devs = ctx.devices(include_virtual=include_virtual, refresh=True)
    if external:
        devs = [d for d in devs if d.removable or d.internal is False]
    data = {"devices": [d.to_dict() for d in devs], "count": len(devs)}
    if ctx.as_json:
        return ctx.out(data, [])
    if not devs:
        return ctx.out(data, ["Nie znaleziono nośników."])
    if full:
        lines = []
        for d in devs:
            lines += [f"\n=== {d.path} – {d.short_name()} ==="] + inspect_lines(d)
        return ctx.out(data, lines)
    rows = [[d.path, f"{_v(d.vendor)} {_v(d.model)}".replace(UNAVAILABLE + " ", ""),
             human_size(d.size_detected_bytes), _v(d.device_type), _v(d.interface),
             "tak" if d.mounted else ("nie" if d.mounted is not None else UNAVAILABLE),
             "SYSTEM" if d.is_system else ""] for d in devs]
    return ctx.out(data, format_table(["URZĄDZENIE", "PRODUCENT / MODEL", "POJEMNOŚĆ", "TYP", "INTERFEJS", "MONT.", ""], rows))


def inspect_lines(d: Device) -> List[str]:
    L: List[str] = []

    def sec(title, pairs):
        L.append(f"\n{title}")
        L.extend(_pairs(pairs))

    sec("Identyfikacja", [("Ścieżka urządzenia", d.path), ("Nazwa systemowa", _v(d.system_name)),
                          ("Nazwa nośnika", _v(d.name)), ("Producent", _v(d.vendor)), ("Model", _v(d.model)),
                          ("Oprogramowanie sprzętowe", _v(d.firmware)), ("Typ urządzenia", _v(d.device_type)),
                          ("Magistrala (wg systemu)", _v(d.bus_type)), ("Interfejs", _v(d.interface))])
    sec("Numery seryjne", serial_pairs(d))
    sec("Identyfikatory sprzętowe", id_pairs(d))
    sec("Pojemność i geometria", [("Pojemność nominalna", _size(d.size_nominal_bytes)),
                                  ("Pojemność wykryta przez system", _size(d.size_detected_bytes)),
                                  ("Rozmiar sektora logicznego", _v(d.sector_size)),
                                  ("Rozmiar sektora fizycznego", _v(d.physical_sector_size)),
                                  ("Rozmiar bloku", _v(d.block_size)), ("Liczba sektorów", _v(d.sector_count))])
    sec("Układ i system plików", [("Tablica partycji", _v(d.partition_table)), ("Liczba partycji", str(len(d.partitions))),
                                  ("System plików", _v(d.filesystem)), ("Etykieta woluminu", _v(d.volume_label)),
                                  ("Szyfrowanie", _v(d.encryption))])
    sec("Stan", [("Zamontowany", _v(d.mounted)), ("Punkty montowania", _v(d.mount_points)),
                 ("Tylko do odczytu", _v(d.read_only)), ("Dysk systemowy", _v(d.is_system)),
                 ("Wymienny", _v(d.removable)), ("Wewnętrzny", _v(d.internal))])
    L.append("\nStan zdrowia")
    L.extend(_pairs(health_pairs(d)))
    L.append(f"\nData i godzina odczytu: {d.collected_at}")
    for n in d.notes:
        L.append(f"UWAGA: {n}")
    return L


def serial_pairs(d: Device) -> List[tuple]:
    return [("Numer seryjny urządzenia", _v(d.serial)), ("Numer seryjny kontrolera", _v(d.controller_serial)),
            ("Numer seryjny karty SD", _v(d.card_serial)), ("Numer seryjny czytnika/mostka USB", _v(d.reader_serial))]


def id_pairs(d: Device) -> List[tuple]:
    return [("USB VID", _v(d.usb_vid)), ("USB PID", _v(d.usb_pid)), ("USB revision", _v(d.usb_revision)),
            ("WWN", _v(d.wwn)), ("EUI-64", _v(d.eui64)), ("UUID (dysk/kontener)", _v(d.uuid)),
            ("PARTUUID (dysk)", _v(d.partuuid))]


def health_pairs(d: Device) -> List[tuple]:
    h = d.health
    if not h:
        return [("Stan zdrowia", UNAVAILABLE)]
    pairs = [("Stan zdrowia", _v(h.status)), ("Temperatura (°C)", _v(h.temperature_c)),
             ("Godziny pracy", _v(h.power_on_hours)), ("Liczba błędów", _v(h.error_count)),
             ("Źródło", _v(h.source))]
    if h.note:
        pairs.append(("Uwagi", h.note))
    return pairs


def cmd_inspect(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    d.health = ctx.backend.read_health(d)
    return ctx.out(d.to_dict(), [f"=== {d.path} – {d.short_name()} ==="] + inspect_lines(d))


def cmd_serial(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    data = {"path": d.path, "serial": d.serial, "controller_serial": d.controller_serial,
            "card_serial": d.card_serial, "reader_serial": d.reader_serial}
    lines = [f"{d.path} – {d.short_name()}"] + _pairs(serial_pairs(d)) + [
        "", "UWAGA: przy kartach SD numer seryjny może być niedostępny albo dotyczyć czytnika/mostka USB,",
        "       a nie samej karty. Pole oznaczone NIEDOSTĘPNE oznacza, że system go nie udostępnia."]
    lines += [f"UWAGA: {n}" for n in d.notes if "seryjny" in n.lower()]
    return ctx.out({k: (v if v is not None else UNAVAILABLE) for k, v in data.items()}, lines)


def cmd_ids(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    data = {"path": d.path, **{k: (v if v is not None else UNAVAILABLE) for k, v in
                              {"usb_vid": d.usb_vid, "usb_pid": d.usb_pid, "usb_revision": d.usb_revision,
                               "wwn": d.wwn, "eui64": d.eui64, "uuid": d.uuid, "partuuid": d.partuuid}.items()}}
    return ctx.out(data, [f"{d.path} – {d.short_name()}"] + _pairs(id_pairs(d)))


def cmd_uuids(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    parts = [{"partition": p.identifier, "fs_uuid": p.fs_uuid or UNAVAILABLE, "partuuid": p.partuuid or UNAVAILABLE,
              "volume_label": p.volume_label or UNAVAILABLE, "part_type": p.part_type or UNAVAILABLE}
             for p in d.partitions]
    lines = [f"{d.path} – {d.short_name()}", f"  UUID dysku/kontenera : {_v(d.uuid)}",
             f"  PARTUUID dysku       : {_v(d.partuuid)}", "",
             "  UUID systemu plików (fs_uuid) ≠ PARTUUID (identyfikator partycji) ≠ etykieta woluminu:"]
    for p in parts:
        lines.append(f"    {p['partition']:<14} fs_uuid={p['fs_uuid']}  partuuid={p['partuuid']}  etykieta={p['volume_label']}")
    if not parts:
        lines.append("    brak partycji")
    return ctx.out({"path": d.path, "uuid": d.uuid or UNAVAILABLE, "partitions": parts}, lines)


def cmd_partitions(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    data = {"path": d.path, "partition_table": d.partition_table or UNAVAILABLE,
            "partitions": [p.__dict__ for p in d.partitions]}
    lines = [f"{d.path} – {d.short_name()}  (tablica partycji: {_v(d.partition_table)})"]
    if d.partitions:
        rows = [[_v(p.identifier), human_size(p.size_bytes), _v(p.part_type), _v(p.fs_type), _short(_v(p.volume_label), 28),
                 _short(_v(p.mount_points), 36), _v(p.encrypted)] for p in d.partitions]
        lines += format_table(["PARTYCJA", "ROZMIAR", "TYP", "SYSTEM PLIKÓW", "ETYKIETA", "MONTOWANIE", "SZYFR."], rows)
    else:
        lines.append("  Brak partycji (lub NIEDOSTĘPNE).")
    return ctx.out(data, lines)


def cmd_mount_status(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    data = {"path": d.path, "mounted": d.mounted if d.mounted is not None else UNAVAILABLE,
            "mount_points": d.mount_points if d.mount_points is not None else UNAVAILABLE,
            "read_only": d.read_only if d.read_only is not None else UNAVAILABLE,
            "is_system": d.is_system if d.is_system is not None else UNAVAILABLE}
    lines = [f"{d.path} – {d.short_name()}", f"  Zamontowany        : {_v(d.mounted)}",
             f"  Punkty montowania  : {_v(d.mount_points)}", f"  Tylko do odczytu   : {_v(d.read_only)}",
             f"  Dysk systemowy     : {_v(d.is_system)}"]
    for p in d.partitions:
        lines.append(f"    {_v(p.identifier)}: {_v(p.mount_points)}")
    return ctx.out(data, lines)


def cmd_system_status(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    return ctx.out({"path": d.path, "is_system": d.is_system if d.is_system is not None else UNAVAILABLE,
                    "internal": d.internal if d.internal is not None else UNAVAILABLE,
                    "removable": d.removable if d.removable is not None else UNAVAILABLE},
                   [f"{d.path} – {d.short_name()}", f"  Dysk systemowy : {_v(d.is_system)}",
                    f"  Wewnętrzny     : {_v(d.internal)}", f"  Wymienny       : {_v(d.removable)}"])


def cmd_space(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    vols = []
    for m in sorted({m for m in (d.mount_points or [])}):
        try:
            u = shutil.disk_usage(m)
            vols.append({"mount": m, "total": u.total, "used": u.used, "free": u.free})
        except OSError as e:
            vols.append({"mount": m, "error": str(e)})
    lines = [f"{d.path} – {d.short_name()}"]
    for v in vols:
        lines.append(f"  {v['mount']}: " + (v["error"] if "error" in v else
                     f"razem {human_size(v['total'])}, użyte {human_size(v['used'])}, wolne {human_size(v['free'])}"))
    if not vols:
        lines.append("  Brak zamontowanych woluminów – wolne miejsce NIEDOSTĘPNE.")
    return ctx.out({"path": d.path, "volumes": vols}, lines)


def cmd_health(ctx: Context, spec: str) -> Dict:
    d = ctx.pick(spec)
    d.health = ctx.backend.read_health(d)
    return ctx.out({"path": d.path, "health": d.to_dict()["health"]},
                   [f"{d.path} – {d.short_name()}"] + _pairs(health_pairs(d)) +
                   ([f"  Szczegóły : {json.dumps(d.health.details, ensure_ascii=False)}"] if d.health.details else []))


# ===================================================================== operacje na nośniku (odczyt)
def _raw_target(ctx: Context, d: Device):
    if not d.size_detected_bytes:
        raise MediaToolError("System nie podał rozmiaru nośnika (NIEDOSTĘPNE) – operacja niemożliwa")
    return ctx.backend.raw_path(d), d.size_detected_bytes, d.sector_size or 512


def _plan(ctx: Context, title: str, items: Dict[str, Any]) -> Dict:
    items = {"dry_run": True, "operation": title, **items}
    lines = [f"[SYMULACJA] {title} – nic nie zostanie wykonane:"] + [f"  {k}: {v}" for k, v in items.items()
                                                                  if k not in ("dry_run", "operation")]
    return ctx.out(items, lines)


def cmd_benchmark(ctx: Context, spec: str, mib: Optional[int] = None) -> Dict:
    d = ctx.pick(spec, Risk.READ, "test prędkości odczytu")
    mib = mib or ctx.cfg["benchmark_size_mib"]
    path, size, sector = _raw_target(ctx, d)
    if ctx.dry_run:
        return _plan(ctx, "test prędkości odczytu", {"urządzenie": path, "odczyt": f"{mib} MiB w 4 miejscach + losowe 4 KiB",
                                                    "dostęp do surowego urządzenia": can_read(path)})
    logs.audit("benchmark", device=d.path, mib=mib)
    try:
        res = bench.benchmark_raw(path, size, sector, mib, progress=ctx.progress)
    except RawAccessError as e:
        if is_admin():
            raise
        if d.is_system:
            raise MediaToolError(f"{e}\nTryb awaryjny (odczyt plików) nie jest używany dla dysku systemowego.")
        ctx.console.warn(f"{e}\nPrzechodzę na tryb awaryjny (odczyt plików z zamontowanych woluminów).")
        mounts = d.mount_points or []
        if not mounts:
            raise MediaToolError("Brak uprawnień do surowego urządzenia i brak zamontowanych woluminów")
        res = bench.benchmark_files(mounts, mib, progress=ctx.progress)
    lines = [f"Test prędkości odczytu – tryb: {res['mode']}", f"  Odczyt sekwencyjny (średnio): {res['seq_avg_mb_s']} MB/s"]
    if "seq_min_mb_s" in res:
        lines.append(f"  min / max                   : {res['seq_min_mb_s']} / {res['seq_max_mb_s']} MB/s")
        for s in res["sequential"]:
            lines.append(f"    od {s['offset']:>14} B : {s['mb_s']} MB/s")
    lines += [f"  Losowy odczyt 4 KiB         : {res['random_4k_iops']} IOPS (śr. {res['latency_avg_ms']} ms, p95 {res['latency_p95_ms']} ms)",
              f"  Błędy odczytu               : {res['read_errors']}", f"  {res['note']}"]
    return ctx.out({"path": d.path, **res}, lines)


def cmd_integrity(ctx: Context, spec: str, ranges: Optional[List[str]] = None, samples: int = 8,
                  passes: int = 2, seed: Optional[int] = None) -> Dict:
    d = ctx.pick(spec, Risk.READ, "test integralności obszarów")
    path, size, sector = _raw_target(ctx, d)
    areas = [scanning.parse_range(r) for r in ranges] if ranges else None
    if ctx.dry_run:
        planned = areas or scanning.default_areas(size, sector, samples, AREA_DEFAULT, seed or 0)
        return _plan(ctx, "test integralności wybranych obszarów", {"urządzenie": path, "obszary": len(planned),
                                                                  "przebiegi": passes, "przykład": planned[:4]})
    logs.audit("integrity", device=d.path)
    res = scanning.integrity(path, size, sector, areas, samples, passes=passes, seed=seed, progress=ctx.progress)
    pt = res["partition_table"]
    lines = [f"Integralność {d.path}: {'OK' if res['passed'] else 'WYKRYTO PROBLEMY'}",
             f"  Sprawdzone obszary          : {res['areas_checked']} (przebiegi: {res['passes']}, seed: {_v(res['seed'])})",
             f"  Obszary z niestabilnym odczytem : {res['unstable_areas']}",
             f"  Obszary z błędami odczytu       : {res['areas_with_read_errors']}",
             f"  Tablica partycji (z danych)  : {pt['scheme']}" + (f", {len(pt['partitions'])} partycji" if pt["partitions"] else "")]
    lines += [f"  ! {p}" for p in pt["problems"]]
    for a in res["areas"]:
        if "error" in a or not a["stable"] or a["unreadable_bytes"]:
            lines.append(f"  ! obszar @{a['offset']}: {a.get('error') or ('niestabilny' if not a['stable'] else '')} "
                         f"nieczytelne: {a.get('unreadable_bytes', 0)} B")
    return ctx.out({"path": d.path, **res}, lines)


def cmd_scan(ctx: Context, spec: str, start: int = 0, length: Optional[int] = None) -> Dict:
    d = ctx.pick(spec, Risk.READ, "skanowanie tylko do odczytu")
    path, size, sector = _raw_target(ctx, d)
    chunk = ctx.cfg["chunk_size_mib"] * 1048576
    if ctx.dry_run:
        return _plan(ctx, "skanowanie tylko do odczytu", {"urządzenie": path, "zakres": f"{start}..{min(size, start + length) if length else size}",
                                                         "rozmiar bloku": chunk})
    logs.audit("scan", device=d.path, start=start, length=length)
    res = scanning.scan(path, size, sector, chunk, start, length, ctx.cfg["max_read_errors"], progress=ctx.progress)
    lines = [f"Skan {d.path}: {'CZYSTO' if res['clean'] else 'WYKRYTO PROBLEMY'}",
             f"  Przeczytano            : {human_size(res['bytes_read'])}", f"  Średnia prędkość       : {res['avg_mb_s']} MB/s",
             f"  Nieczytelne sektory    : {res['unreadable_sectors']}"]
    for o, l in res["unreadable_ranges"][:20]:
        lines.append(f"    ! offset {o}, długość {l} B")
    for s in res["slow_segments"][:10]:
        lines.append(f"    ~ wolny segment @{s['offset']}: {s['mb_s']} MB/s (mediana {res['median_segment_mb_s']})")
    if res["aborted"]:
        lines.append(f"  Przerwano: {res['aborted']}")
    return ctx.out({"path": d.path, **res}, lines)


def cmd_errors(ctx: Context, spec: str) -> Dict:
    """Błędy odczytu: liczniki SMART + szybka próba odczytu próbek."""
    d = ctx.pick(spec, Risk.READ, "sprawdzenie błędów odczytu")
    d.health = ctx.backend.read_health(d)
    smart_errors = d.health.error_count if d.health else None
    data: Dict[str, Any] = {"path": d.path, "smart_error_count": smart_errors if smart_errors is not None else UNAVAILABLE}
    lines = [f"Błędy odczytu {d.path}", f"  Licznik błędów SMART : {_v(smart_errors)}  (źródło: {_v(d.health.source if d.health else None)})"]
    path, size, sector = _raw_target(ctx, d)
    if ctx.dry_run:
        return _plan(ctx, "sprawdzenie błędów odczytu", {"urządzenie": path, "próbki": "początek, środek, koniec + 8 losowych"})
    try:
        res = scanning.integrity(path, size, sector, samples=8, passes=1, progress=ctx.progress)
        data["sample_read"] = {"areas": res["areas_checked"], "with_errors": res["areas_with_read_errors"]}
        lines.append(f"  Próbki odczytu       : {res['areas_checked']} obszarów, z błędami: {res['areas_with_read_errors']}")
    except RawAccessError as e:
        data["sample_read"] = UNAVAILABLE
        lines.append(f"  Próbki odczytu       : NIEDOSTĘPNE ({e})")
    return ctx.out(data, lines)


# ===================================================================== obrazy
def _img_common(ctx: Context, d: Device):
    mounts = list(d.mount_points or [])
    meta = {"identity": d.stable_identity(), "fingerprint": d.identity_fingerprint(), "device_path": d.path,
            "vendor": d.vendor, "model": d.model, "serial": d.serial, "size_bytes": d.size_detected_bytes,
            "sector_size": d.sector_size}
    return mounts, meta


def cmd_image_create(ctx: Context, spec: str, output: str, overwrite: bool = False, resume: bool = False,
                     algo: Optional[str] = None) -> Dict:
    action = "wznowienie obrazu" if resume else "tworzenie obrazu"
    d = ctx.pick(spec, Risk.FILE_WRITE, f"{action} → {output}")
    path, size, sector = _raw_target(ctx, d)
    mounts, meta = _img_common(ctx, d)
    chunk = ctx.cfg["chunk_size_mib"] * 1048576
    algo = algo or ctx.cfg["hash_algorithm"]
    if ctx.dry_run:
        plan = imaging.plan_create(path, size, output, resume=resume, overwrite=overwrite, avoid_paths=mounts, chunk_size=chunk)
        return ctx.out({"dry_run": True, **plan}, [
            f"[SYMULACJA] {action}:", f"  źródło (tylko odczyt) : {path}", f"  rozmiar               : {_size(size)}",
            f"  plik obrazu           : {output} (+ {imaging.sidecar_path(output)})",
            f"  wznowienie od         : {plan['resume_from']} B", f"  wolne miejsce         : {_v(plan['free_bytes'])}",
            f"  dostęp do źródła      : {'tak' if plan['source_readable'] else 'NIE – potrzebne uprawnienia administratora'}",
            f"  wynik kontroli        : {'OK' if plan['ok'] else 'PROBLEMY: ' + '; '.join(plan['problems'])}"])
    logs.audit("image_create", device=d.path, output=output, resume=resume, size=size)
    state = imaging.create_image(path, size, output, sector_size=sector, chunk_size=chunk,
                                 max_errors=ctx.cfg["max_read_errors"], source_meta=meta, resume=resume,
                                 overwrite=overwrite, avoid_paths=mounts, algo=algo, progress=ctx.progress)
    bad = state["bad_ranges"]
    lines = [f"Obraz gotowy: {output}", f"  Rozmiar     : {_size(size)}",
             f"  Suma {state['image']['hash_algorithm']} : {state['image']['hash']}",
             f"  Nieczytelne sektory: {state['read_errors']}"]
    if bad:
        lines.append("  UWAGA: obraz zawiera zera w miejscach nieczytelnych – lista w pliku .mtinfo.json")
    logs.audit("image_done", device=d.path, output=output, hash=state["image"]["hash"], bad=len(bad))
    return ctx.out({"path": d.path, "output": output, **state}, lines)


def cmd_image_verify(ctx: Context, spec: str, image: str) -> Dict:
    d = ctx.pick(spec, Risk.READ, f"weryfikacja obrazu {image}")
    path, size, sector = _raw_target(ctx, d)
    if ctx.dry_run:
        return _plan(ctx, "weryfikacja obrazu względem nośnika", {"źródło": path, "obraz": image,
                                                                "rozmiar obrazu": file_size(image)})
    logs.audit("image_verify", device=d.path, image=image)
    res = imaging.verify_image(path, size, image, sector_size=sector, chunk_size=ctx.cfg["chunk_size_mib"] * 1048576,
                               progress=ctx.progress)
    lines = [f"Weryfikacja {image} względem {d.path}: {'ZGODNY' if res['match'] else 'RÓŻNICE / NIEKOMPLETNY'}",
             f"  Rozmiar nośnika / obrazu : {res['device_size']} / {res['image_size']}"
             f" ({'zgodne' if res['size_match'] else 'RÓŻNE'})",
             f"  Porównano                : {human_size(res['compared_bytes'])}",
             f"  Bloki z różnicami        : {res['mismatched_chunks']}"
             + (f" (pierwsza różnica @ {res['first_mismatch_offset']})" if res["first_mismatch_offset"] is not None else ""),
             f"  Znane nieczytelne zakresy: {human_size(res['known_bad_bytes'])} (pominięte)",
             f"  Nowe błędy odczytu       : {res['device_read_errors']} sektorów"]
    if not res["complete_image"]:
        lines.append("  UWAGA: obraz jest niekompletny (przerwane tworzenie)")
    if res["hash_matches_record"] is not None:
        lines.append(f"  Suma zgodna z zapisem w .mtinfo.json: {'tak' if res['hash_matches_record'] else 'NIE'}")
    return ctx.out({"path": d.path, **res}, lines)


def cmd_image_inspect(ctx: Context, image: str, compute_hash: bool = False) -> Dict:
    res = imaging.inspect_image(image, compute_hash=compute_hash, progress=ctx.progress)
    pt = res["partition_table"]
    lines = [f"Obraz: {res['image']}", f"  Rozmiar              : {_size(res['size_bytes'])}",
             f"  Metadane media-tool  : {'tak' if res['metadata_present'] else 'brak'}",
             f"  Kompletny            : {_v(res.get('complete'))}",
             f"  Tablica partycji     : {pt['scheme']}" + (f" (sektor {pt['sector_size']} B)" if pt["sector_size"] else "")]
    for p in pt["partitions"]:
        nm = f" '{p['name']}'" if p.get("name") else ""
        size_txt = human_size(p["size_bytes"]) if "size_bytes" in p else f"{p.get('sectors', '?')} sektorów"
        lines.append(f"    #{p['index']}: {p['type']}{nm}  start LBA {p['start_lba']}  {size_txt}")
    lines += [f"  ! {p}" for p in pt["problems"]]
    meta = res.get("metadata")
    if meta:
        src = meta.get("source", {})
        lines += [f"  Źródło               : {_v(src.get('vendor'))} {_v(src.get('model'))} (nr seryjny: {_v(src.get('serial'))})",
                  f"  Utworzony            : {meta.get('created_at')}", f"  Suma {meta['image']['hash_algorithm']}: {_v(meta['image'].get('hash'))}",
                  f"  Nieczytelne sektory  : {meta.get('read_errors')}"]
    if "computed_hash" in res:
        lines.append(f"  Obliczona suma       : {res['computed_hash']['value']}"
                     + (f"  (zgodna z zapisem: {'tak' if res.get('hash_matches_record') else 'NIE'})" if "hash_matches_record" in res else ""))
    if res.get("note"):
        lines.append(f"  {res['note']}")
    return ctx.out(res, lines)


def cmd_hash(ctx: Context, path: str, algo: Optional[str] = None) -> Dict:
    algo = algo or ctx.cfg["hash_algorithm"]
    if ctx.dry_run:
        return _plan(ctx, "obliczenie sumy kontrolnej", {"plik": path, "algorytm": algo, "rozmiar": file_size(path)})
    digest = imaging.hash_file(path, algo, ctx.progress)
    return ctx.out({"file": path, "algorithm": algo, "hash": digest}, [f"{digest}  {path}  ({algo})"])


def cmd_compare(ctx: Context, a: str, b: str, algo: Optional[str] = None) -> Dict:
    algo = algo or ctx.cfg["hash_algorithm"]
    if ctx.dry_run:
        return _plan(ctx, "porównanie obrazów", {"A": a, "B": b, "rozmiary": [file_size(a), file_size(b)]})
    res = imaging.compare_images(a, b, algo=algo, chunk_size=ctx.cfg["chunk_size_mib"] * 1048576, progress=ctx.progress)
    lines = [f"Porównanie: {'IDENTYCZNE' if res['identical'] else 'RÓŻNE'}",
             f"  A: {a}  ({res['size_a']} B)  {res['hash_a']}", f"  B: {b}  ({res['size_b']} B)  {res['hash_b']}"]
    if not res["identical"]:
        lines.append(f"  Pierwsza różnica @ {res['first_difference_offset']}, bloki różne: {res['different_chunks']}")
    return ctx.out(res, lines)


# ===================================================================== raporty
def default_report_path(ctx: Context, fmt: str) -> str:
    return os.path.join(ctx.cfg["report_dir"], f"media-tool-report-{datetime.datetime.now():%Y%m%d_%H%M%S}.{fmt}")


def cmd_report(ctx: Context, fmt: str, output: Optional[str] = None, spec: Optional[str] = None,
               overwrite: bool = False, redact: Optional[bool] = None, health: bool = True,
               results: Optional[Dict[str, Any]] = None) -> Dict:
    from .util import atomic_write_text
    if fmt not in reports.FORMATS:
        raise MediaToolError(f"Nieznany format raportu: {fmt} (dostępne: {', '.join(reports.FORMATS)})")
    devs = [ctx.pick(spec)] if spec else ctx.devices(refresh=True)
    if health:
        for d in devs:
            try:
                d.health = ctx.backend.read_health(d)
            except Exception as e:
                logs.get().warning("health failed for %s: %s", d.path, e)
    redact = ctx.cfg["redact_serials"] if redact is None else redact
    output = output or default_report_path(ctx, fmt)
    if ctx.dry_run:
        return _plan(ctx, f"raport {fmt.upper()}", {"urządzeń": len(devs), "plik": output, "maskowanie numerów seryjnych": redact})
    if os.path.exists(output) and not overwrite:
        raise MediaToolError(f"Plik {output} już istnieje (użyj --overwrite)")
    snap = reports.build_snapshot(devs, ctx.backend.name, redact=redact, extra=results)
    atomic_write_text(output, reports.render(snap, fmt))
    logs.audit("report", format=fmt, output=output, devices=len(devs))
    return ctx.out({"output": output, "format": fmt, "devices": len(devs), "redacted": redact},
                   [f"Zapisano raport {fmt.upper()}: {output}  ({len(devs)} urządzeń"
                    f"{', numery seryjne zamaskowane' if redact else ''})"])


def cmd_report_diff(ctx: Context, old_path: str, new_path: str) -> Dict:
    def load(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as e:
            raise MediaToolError(f"Nie można wczytać raportu JSON {p}: {e}")
    res = reports.diff_reports(load(old_path), load(new_path))
    lines = [f"Porównanie raportów: {res['old_generated_at']} → {res['new_generated_at']}",
             "  Brak zmian." if res["identical"] else ""]
    lines += [f"  + nowe urządzenie: {p}" for p in res["added"]] + [f"  - usunięte urządzenie: {p}" for p in res["removed"]]
    lines += [f"  ~ {c['device']} · {c['field']}: {c['old']} → {c['new']}" for c in res["changes"]]
    return ctx.out(res, [l for l in lines if l != ""])


# ===================================================================== odmontowanie / odłączenie
def _state_change(ctx: Context, spec: str, action: str, power_off: bool) -> Dict:
    d = ctx.pick(spec, Risk.DEVICE_STATE, action)
    if ctx.dry_run:
        return _plan(ctx, action, {"urządzenie": d.path, "punkty montowania": d.mount_points or [],
                                  "odłączenie zasilania": power_off})
    logs.audit("state_change", action=action, device=d.path)
    msgs = ctx.backend.unmount(d)
    if power_off:
        msgs.append(ctx.backend.power_off(d))
    logs.audit("state_change_done", action=action, device=d.path)
    return ctx.out({"path": d.path, "action": action, "messages": msgs},
                   [f"{action}: {d.path} – gotowe"] + [f"  {m}" for m in msgs if m] +
                   (["Możesz bezpiecznie odłączyć nośnik."] if power_off else []))


def cmd_unmount(ctx: Context, spec: str) -> Dict:
    return _state_change(ctx, spec, "odmontowanie", power_off=False)


def cmd_eject(ctx: Context, spec: str) -> Dict:
    return _state_change(ctx, spec, "przygotowanie do bezpiecznego odłączenia", power_off=True)


# ===================================================================== logi / konfiguracja / diagnostyka
def cmd_logs(ctx: Context, lines: int = 50, show_path: bool = False) -> Dict:
    if show_path:
        return ctx.out({"log_file": str(logs.log_file())}, [str(logs.log_file())])
    tail = logs.tail(lines)
    return ctx.out({"log_file": str(logs.log_file()), "lines": tail},
                   tail or [f"Brak wpisów w {logs.log_file()}"])


def cmd_config(ctx: Context, action: str = "show", key: Optional[str] = None, value: Optional[str] = None) -> Dict:
    if action == "path":
        return ctx.out({"config_file": str(config.config_path())}, [str(config.config_path())])
    if action == "reset":
        config.reset()
        ctx.cfg = config.load()
        return ctx.out({"reset": True}, ["Przywrócono ustawienia domyślne."])
    if action == "get":
        if key not in config.SCHEMA:
            raise MediaToolError(f"Nieznany klucz: {key}. Dostępne: {', '.join(config.SCHEMA)}")
        return ctx.out({key: ctx.cfg[key]}, [str(ctx.cfg[key])])
    if action == "set":
        if not key or value is None:
            raise MediaToolError("Użycie: config set KLUCZ WARTOŚĆ")
        try:
            val = config.set_value(key, value)
        except config.ConfigError as e:
            raise MediaToolError(str(e))
        ctx.cfg = config.load()
        return ctx.out({key: val}, [f"{key} = {val}"])
    cfg = config.load()
    lines = [f"Plik konfiguracji: {config.config_path()}", ""]
    lines += [f"  {k:<20} = {cfg[k]!s:<10}  {config.SCHEMA[k][1]}" for k in config.SCHEMA]
    return ctx.out({"config_file": str(config.config_path()), "config": cfg}, lines)


def cmd_features(ctx: Context) -> Dict:
    feats = capabilities.features()
    return ctx.out({k: {"title": f.title, "status": f.status, "reason": f.reason} for k, f in feats.items()},
                   ["Dostępność funkcji na tym systemie:"] + capabilities.summary_lines())


def cmd_diagnose(ctx: Context) -> Dict:
    import platform
    tools = ctx.backend.tool_status()
    try:
        devs = ctx.backend.list_devices(include_virtual=True)
        listing = {"ok": True, "count": len(devs)}
    except Exception as e:
        devs, listing = [], {"ok": False, "error": str(e)}
    raw = {d.path: can_read(ctx.backend.raw_path(d)) for d in devs}
    data = {"os": platform.platform(), "python": sys.version.split()[0], "backend": ctx.backend.name,
            "administrator": is_admin(), "tools": tools, "device_listing": listing, "raw_read_access": raw,
            "config_file": str(config.config_path()), "log_file": str(logs.log_file()),
            "features": {k: f.status for k, f in capabilities.features().items()}}
    lines = ["TRYB DIAGNOSTYCZNY", f"  System        : {data['os']}", f"  Python        : {data['python']}",
             f"  Backend       : {data['backend']}", f"  Administrator : {_v(data['administrator'])}", "  Narzędzia:"]
    lines += [f"    {t:<16} {p or 'BRAK'}" for t, p in tools.items()]
    lines.append(f"  Lista urządzeń: " + (f"OK ({listing['count']} szt.)" if listing["ok"] else f"BŁĄD – {listing['error']}"))
    lines += [f"    odczyt surowy {p}: {'tak' if ok else 'NIE (brak uprawnień)'}" for p, ok in raw.items()]
    lines += [f"  Konfiguracja  : {data['config_file']}", f"  Log           : {data['log_file']}", ""]
    lines += capabilities.summary_lines()
    return ctx.out(data, lines)

"""Raporty (JSON / CSV / HTML / TXT) oraz porównywanie raportów."""
from __future__ import annotations

import csv
import datetime
import html
import io
import json
import platform
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .models import UNAVAILABLE, Device
from .util import human_size, is_admin, mask

SERIAL_FIELDS = ("serial", "controller_serial", "card_serial", "reader_serial", "wwn", "eui64")
FORMATS = ("json", "csv", "html", "txt")

DEVICE_COLUMNS = [
    "path", "system_name", "name", "vendor", "model", "firmware", "serial", "controller_serial", "card_serial",
    "reader_serial", "usb_vid", "usb_pid", "usb_revision", "wwn", "eui64", "uuid", "partuuid", "device_type",
    "bus_type", "interface", "size_nominal_bytes", "size_detected_bytes", "sector_size", "physical_sector_size",
    "block_size", "sector_count", "partition_table", "filesystem", "volume_label", "mount_points", "mounted",
    "read_only", "is_system", "removable", "internal", "encryption", "collected_at",
]
HEALTH_COLUMNS = ["status", "temperature_c", "power_on_hours", "error_count", "source", "note"]
PART_COLUMNS = ["identifier", "path", "size_bytes", "offset_bytes", "part_type", "fs_type", "volume_label",
                "fs_uuid", "partuuid", "mount_points", "read_only", "encrypted"]


def build_snapshot(devices: Iterable[Device], backend_name: str, redact: bool = False,
                   extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    devs = [d.to_dict(display=True) for d in devices]
    if redact:
        for d in devs:
            for f in SERIAL_FIELDS:
                if d.get(f) != UNAVAILABLE:
                    d[f] = mask(d[f])
    snap = {
        "report_version": 1, "tool": "media-tool", "tool_version": __version__,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": {"os": platform.platform(), "backend": backend_name, "python": platform.python_version(),
                   "administrator": is_admin()},
        "redacted": redact, "devices": devs,
    }
    if extra:
        snap["results"] = extra
    return snap


# ---------------------------------------------------------------- renderowanie
def render(snapshot: Dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    if fmt == "csv":
        return _csv(snapshot)
    if fmt == "txt":
        return _txt(snapshot)
    if fmt == "html":
        return _html(snapshot)
    raise ValueError(f"Nieznany format: {fmt}")


def _cell(v: Any) -> str:
    if isinstance(v, list):
        v = "; ".join(str(x) for x in v)
    s = "" if v is None else str(v)
    # ochrona przed wstrzyknięciem formuł w arkuszach (Excel/LibreOffice)
    if s and s[0] in "=+-@\t\r":
        s = "'" + s
    return s


def _csv(snap: Dict[str, Any]) -> str:
    buf = io.StringIO()
    cols = ["record_type", "device"] + DEVICE_COLUMNS[1:] + [f"health_{c}" for c in HEALTH_COLUMNS] + \
           [f"part_{c}" for c in PART_COLUMNS]
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(cols)
    for d in snap["devices"]:
        h = d.get("health") if isinstance(d.get("health"), dict) else {}
        row = ["device", d["path"]] + [_cell(d.get(c)) for c in DEVICE_COLUMNS[1:]]
        row += [_cell(h.get(c, UNAVAILABLE)) for c in HEALTH_COLUMNS] + [""] * len(PART_COLUMNS)
        w.writerow(row)
        for p in d.get("partitions", []):
            prow = ["partition", d["path"]] + [""] * (len(DEVICE_COLUMNS) - 1 + len(HEALTH_COLUMNS))
            prow += [_cell(p.get(c)) for c in PART_COLUMNS]
            w.writerow(prow)
    return buf.getvalue()


LABELS = {
    "path": "Ścieżka urządzenia", "system_name": "Nazwa systemowa", "name": "Nazwa nośnika", "vendor": "Producent",
    "model": "Model", "firmware": "Oprogramowanie sprzętowe", "serial": "Numer seryjny urządzenia",
    "controller_serial": "Numer seryjny kontrolera", "card_serial": "Numer seryjny karty SD",
    "reader_serial": "Numer seryjny czytnika/mostka", "usb_vid": "USB VID", "usb_pid": "USB PID",
    "usb_revision": "USB revision", "wwn": "WWN", "eui64": "EUI-64", "uuid": "UUID (dysk/kontener)",
    "partuuid": "PARTUUID (dysk)", "device_type": "Typ urządzenia", "bus_type": "Magistrala (wg systemu)",
    "interface": "Interfejs", "size_nominal_bytes": "Pojemność nominalna (B)",
    "size_detected_bytes": "Pojemność wykryta przez system (B)", "sector_size": "Rozmiar sektora logicznego (B)",
    "physical_sector_size": "Rozmiar sektora fizycznego (B)", "block_size": "Rozmiar bloku (B)",
    "sector_count": "Liczba sektorów", "partition_table": "Tablica partycji", "filesystem": "System plików",
    "volume_label": "Etykieta woluminu", "mount_points": "Punkty montowania", "mounted": "Zamontowany",
    "read_only": "Tylko do odczytu", "is_system": "Dysk systemowy", "removable": "Wymienny", "internal": "Wewnętrzny",
    "encryption": "Szyfrowanie", "collected_at": "Data i godzina odczytu",
}
HEALTH_LABELS = {"status": "Stan zdrowia", "temperature_c": "Temperatura (°C)", "power_on_hours": "Godziny pracy",
                 "error_count": "Liczba błędów", "source": "Źródło danych zdrowia", "note": "Uwagi"}


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "tak" if v else "nie"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "brak"
    return str(v)


def _device_pairs(d: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs = [(LABELS[c], _fmt(d.get(c, UNAVAILABLE))) for c in DEVICE_COLUMNS]
    h = d.get("health")
    if isinstance(h, dict):
        pairs += [(HEALTH_LABELS[c], _fmt(h.get(c, UNAVAILABLE))) for c in HEALTH_COLUMNS]
    else:
        pairs.append(("Stan zdrowia", UNAVAILABLE))
    return pairs


def _title(d: Dict[str, Any]) -> str:
    return " ".join(str(d[k]) for k in ("vendor", "model") if d.get(k) not in (None, UNAVAILABLE)) or UNAVAILABLE


def _txt(snap: Dict[str, Any]) -> str:
    out = [f"RAPORT NOŚNIKÓW – media-tool {snap['tool_version']}", f"Wygenerowano: {snap['generated_at']}",
           f"System: {snap['system']['os']} ({snap['system']['backend']})",
           f"Administrator: {_fmt(snap['system']['administrator'])}",
           f"Numery seryjne zamaskowane: {_fmt(snap['redacted'])}", ""]
    for d in snap["devices"]:
        out.append("=" * 72)
        out.append(f"{d['path']}  –  {_title(d)}")
        out.append("=" * 72)
        for k, v in _device_pairs(d):
            out.append(f"{k:<40} {v}")
        for p in d.get("partitions", []):
            out.append(f"  Partycja {p.get('identifier')}: {human_size(p['size_bytes']) if isinstance(p.get('size_bytes'), int) else UNAVAILABLE}"
                       f", {p.get('fs_type')}, etykieta={p.get('volume_label')}, fs_uuid={p.get('fs_uuid')}, "
                       f"partuuid={p.get('partuuid')}, mount={_fmt(p.get('mount_points'))}")
        for n in d.get("notes", []):
            out.append(f"  UWAGA: {n}")
        out.append("")
    if snap.get("results"):
        out.append("WYNIKI TESTÓW")
        out.append(json.dumps(snap["results"], indent=2, ensure_ascii=False))
    return "\n".join(out) + "\n"


def _html(snap: Dict[str, Any]) -> str:
    e = html.escape
    parts = [f"<h1>Raport nośników</h1><p class=m>media-tool {e(snap['tool_version'])} · "
             f"{e(snap['generated_at'])} · {e(snap['system']['os'])} · administrator: "
             f"{e(_fmt(snap['system']['administrator']))}"
             f"{' · numery seryjne zamaskowane' if snap['redacted'] else ''}</p>"]
    for d in snap["devices"]:
        parts.append(f"<section><h2>{e(d['path'])} <small>{e(_title(d))}</small></h2><table>")
        for k, v in _device_pairs(d):
            cls = " class=na" if v == UNAVAILABLE else ""
            parts.append(f"<tr><th>{e(k)}</th><td{cls}>{e(v)}</td></tr>")
        parts.append("</table>")
        if d.get("partitions"):
            parts.append("<h3>Partycje</h3><table><tr>" + "".join(f"<th>{e(c)}</th>" for c in PART_COLUMNS) + "</tr>")
            for p in d["partitions"]:
                parts.append("<tr>" + "".join(f"<td>{e(_fmt(p.get(c, UNAVAILABLE)))}</td>" for c in PART_COLUMNS) + "</tr>")
            parts.append("</table>")
        for n in d.get("notes", []):
            parts.append(f"<p class=w>⚠ {e(n)}</p>")
        parts.append("</section>")
    if snap.get("results"):
        parts.append(f"<section><h2>Wyniki testów</h2><pre>{e(json.dumps(snap['results'], indent=2, ensure_ascii=False))}</pre></section>")
    css = ("body{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 16px;"
           "background:#fff;color:#1d1d1f}@media(prefers-color-scheme:dark){body{background:#1c1c1e;color:#f5f5f7}"
           "th{color:#a1a1a6}td,th{border-color:#3a3a3c}}h2 small{font-weight:400;color:#6e6e73}"
           "table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}th,td{text-align:left;padding:.25rem .5rem;"
           "border-bottom:1px solid #d2d2d7;vertical-align:top;font-size:.9rem;word-break:break-word}"
           "th{color:#6e6e73;font-weight:500;width:34%}.na{color:#999;font-style:italic}.m{color:#6e6e73}"
           ".w{background:rgba(255,170,0,.15);padding:.4rem .6rem;border-radius:6px}pre{overflow:auto}")
    return (f"<!doctype html><html lang=pl><head><meta charset=utf-8><meta name=viewport "
            f"content='width=device-width,initial-scale=1'><title>Raport nośników</title><style>{css}</style>"
            f"</head><body>{''.join(parts)}</body></html>\n")


# ---------------------------------------------------------------- porównanie raportów
VOLATILE = {"collected_at", "health.temperature_c"}


def _flat(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if k in ("partitions", "notes", "extra"):
            continue
        if k == "health" and isinstance(v, dict):
            for hk, hv in v.items():
                if hk != "details":
                    out[f"health.{hk}"] = hv
            continue
        out[k] = sorted(v) if isinstance(v, list) else v
    for p in d.get("partitions", []):
        pid = p.get("identifier")
        for pk, pv in p.items():
            out[f"partition[{pid}].{pk}"] = sorted(pv) if isinstance(pv, list) else pv
    return out


def _key(d: Dict[str, Any]) -> str:
    for f in ("serial", "card_serial", "wwn"):
        if d.get(f) not in (None, UNAVAILABLE):
            return f"{f}:{d[f]}"
    return f"path:{d.get('path')}"


def diff_reports(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    a = {_key(d): d for d in old.get("devices", [])}
    b = {_key(d): d for d in new.get("devices", [])}
    changed = []
    for k in sorted(a.keys() & b.keys()):
        fa, fb = _flat(a[k]), _flat(b[k])
        for f in sorted(fa.keys() | fb.keys()):
            if f in VOLATILE:
                continue
            va, vb = fa.get(f, UNAVAILABLE), fb.get(f, UNAVAILABLE)
            if va != vb:
                changed.append({"device": b[k]["path"], "key": k, "field": f, "old": va, "new": vb})
    return {"old_generated_at": old.get("generated_at"), "new_generated_at": new.get("generated_at"),
            "added": [b[k]["path"] for k in sorted(b.keys() - a.keys())],
            "removed": [a[k]["path"] for k in sorted(a.keys() - b.keys())],
            "changes": changed, "identical": not changed and a.keys() == b.keys()}

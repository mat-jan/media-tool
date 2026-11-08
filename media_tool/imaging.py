"""Obrazy nośników: tworzenie (z wznawianiem), weryfikacja, inspekcja, porównanie, sumy kontrolne.

Nośnik jest otwierany WYŁĄCZNIE do odczytu. Jedyne, co program zapisuje, to plik obrazu
oraz plik pomocniczy `<obraz>.mtinfo.json` (metadane, postęp, nieczytelne zakresy, suma kontrolna).
"""
from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .errors import Cancelled, ImagingError
from .partition_tables import inspect_table
from .rawio import RawReader, add_range, file_size, overlaps
from .util import atomic_write_json

SIDECAR_SUFFIX = ".mtinfo.json"
STATE_EVERY = 64 * 1024 * 1024
Progress = Optional[Callable[[str, float, str], None]]


def sidecar_path(image: str) -> str:
    return image + SIDECAR_SUFFIX


def load_sidecar(image: str) -> Optional[Dict[str, Any]]:
    import json
    try:
        with open(sidecar_path(image), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _speed(done: int, t0: float) -> str:
    el = time.monotonic() - t0
    return f"{done / 1048576 / el:7.1f} MB/s" if el > 0 else ""


def _under(path: str, roots: List[str]) -> Optional[str]:
    """Czy `path` leży na którymś z woluminów `roots`? Porównuje urządzenie (st_dev) katalogu
    docelowego oraz prefiks ścieżki (z pominięciem katalogu głównego)."""
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.exists(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    try:
        out_dev = os.stat(d).st_dev
    except OSError:
        out_dev = None
    rp = os.path.realpath(path)
    for r in roots:
        try:
            if out_dev is not None and os.stat(r).st_dev == out_dev:
                return r
        except OSError:
            pass
        rr = os.path.realpath(r)
        if rr != os.sep and (rp == rr or rp.startswith(rr.rstrip(os.sep) + os.sep)):
            return r
    return None


# ------------------------------------------------------------------ plan / tworzenie
def plan_create(source: str, size: int, output: str, *, resume: bool = False, overwrite: bool = False,
                avoid_paths: Optional[List[str]] = None, chunk_size: int = 4 * 1048576) -> Dict[str, Any]:
    """Sprawdza warunki wstępne bez czytania danych (używane też przez dry-run)."""
    problems: List[str] = []
    out_dir = os.path.dirname(os.path.abspath(output))
    side = load_sidecar(output)
    done = 0
    if resume:
        if not os.path.exists(output) or not side:
            problems.append("Brak pliku obrazu lub pliku .mtinfo.json – nie ma czego wznawiać")
        elif side.get("progress", {}).get("complete"):
            problems.append("Obraz jest już kompletny")
        else:
            done = side.get("progress", {}).get("bytes_done", 0)
    else:
        if os.path.exists(output) and not overwrite:
            hint = " Użyj 'image resume' aby wznowić." if side and not side.get("progress", {}).get("complete") else ""
            problems.append(f"Plik {output} już istnieje (nie nadpisuję bez --overwrite).{hint}")
    bad_loc = _under(output, avoid_paths or [])
    if bad_loc:
        problems.append(f"Plik obrazu nie może leżeć na obrazowanym nośniku (wolumin {bad_loc})")
    if not os.path.isdir(out_dir):
        problems.append(f"Katalog docelowy nie istnieje: {out_dir}")
        free = None
    else:
        free = shutil.disk_usage(out_dir).free
        need = size - done
        if free < need + 16 * 1048576:
            problems.append(f"Za mało miejsca: potrzeba {need} B, wolne {free} B")
    readable = os.access(source, os.R_OK)
    if not readable:
        problems.append(f"Brak uprawnień do odczytu {source} (uruchom z sudo / jako administrator)")
    return {"source": source, "output": output, "size_bytes": size, "resume_from": done,
            "chunk_size": chunk_size, "free_bytes": free, "source_readable": readable,
            "problems": problems, "ok": not problems}


def create_image(source: str, size: int, output: str, *, sector_size: int = 512, chunk_size: int = 4 * 1048576,
                 max_errors: int = 1000, source_meta: Optional[Dict[str, Any]] = None, resume: bool = False,
                 overwrite: bool = False, avoid_paths: Optional[List[str]] = None, algo: str = "sha256",
                 progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    plan = plan_create(source, size, output, resume=resume, overwrite=overwrite, avoid_paths=avoid_paths,
                       chunk_size=chunk_size)
    if not plan["ok"]:
        raise ImagingError("; ".join(plan["problems"]))
    if size <= 0:
        raise ImagingError("Nieznany lub zerowy rozmiar nośnika")
    chunk_size = max(sector_size, (chunk_size // sector_size) * sector_size)

    h = hashlib.new(algo)
    bad: List[list] = []
    start = 0
    created_at = _now()
    if resume:
        side = load_sidecar(output)
        saved = (side or {}).get("source", {}).get("identity")
        cur = (source_meta or {}).get("identity")
        if saved is not None and cur is not None and saved != cur:
            raise ImagingError("Urządzenie różni się od tego, z którego zaczęto obraz (inna tożsamość) – "
                               "nie wznawiam, aby nie zmieszać danych z dwóch nośników")
        if side["image"]["size_bytes"] != size:
            raise ImagingError("Rozmiar nośnika różni się od rozmiaru zapisanego w obrazie")
        start = (side["progress"]["bytes_done"] // chunk_size) * chunk_size
        bad = [r for r in side.get("bad_ranges", []) if r[0] < start]
        created_at = side.get("created_at", created_at)
        if file_size(output) is None or file_size(output) < start:
            raise ImagingError("Plik obrazu jest krótszy niż zapisany postęp – nie można wznowić")
        h = _rehash(output, start, algo, progress)   # suma dotychczasowej części obrazu

    state = {"format": 1, "tool": "media-tool", "tool_version": __version__, "created_at": created_at,
             "source": {"path": source, **(source_meta or {})},
             "image": {"size_bytes": size, "chunk_size": chunk_size, "sector_size": sector_size,
                       "hash_algorithm": algo, "hash": None},
             "progress": {"bytes_done": start, "complete": False}, "bad_ranges": bad, "read_errors": 0}

    def save(done: int, complete: bool = False) -> None:
        state["updated_at"] = _now()
        state["progress"] = {"bytes_done": done, "complete": complete}
        state["bad_ranges"] = bad
        state["read_errors"] = sum(r[1] for r in bad) // sector_size
        atomic_write_json(sidecar_path(output), state)

    mode = "r+b" if resume else "wb"
    t0 = time.monotonic()
    done = start
    last_state = start
    reader = RawReader(source)
    try:
        with open(output, mode) as out:
            out.seek(start)
            save(start)
            offset = start
            while offset < size:
                if cancel():
                    raise Cancelled("Przerwano przez użytkownika – postęp zapisany, można wznowić")
                n = min(chunk_size, size - offset)
                data = reader.read_resilient(offset, n, sector_size, bad)
                if len(data) < n:
                    raise ImagingError(f"Nośnik zakończył dane na {offset + len(data)} B, "
                                       f"a deklarowany rozmiar to {size} B")
                if sum(r[1] for r in bad) // sector_size > max_errors:
                    raise ImagingError(f"Przekroczono limit {max_errors} nieczytelnych sektorów – przerwano")
                out.write(data)
                h.update(data)
                offset += n
                done = offset
                if progress:
                    progress("obraz", done / size, _speed(done - start, t0))
                if done - last_state >= STATE_EVERY:
                    out.flush()
                    os.fsync(out.fileno())
                    save(done)
                    last_state = done
            out.flush()
            os.fsync(out.fileno())
            out.truncate(size)
        state["image"]["hash"] = h.hexdigest()
        state["finished_at"] = _now()
        state["duration_s"] = round(time.monotonic() - t0, 2)
        save(size, complete=True)
    except BaseException:
        # zachowaj stan wznowienia (również przy Ctrl+C i błędach); plik jest już zamknięty i zflushowany
        try:
            save(done)
        except OSError:
            pass
        raise
    finally:
        reader.close()
    return state


def _rehash(path: str, length: int, algo: str, progress: Progress) -> "hashlib._Hash":
    h = hashlib.new(algo)
    left = length
    with open(path, "rb") as f:
        while left > 0:
            b = f.read(min(left, 4 * 1048576))
            if not b:
                raise ImagingError("Plik obrazu jest krótszy niż zapisany postęp")
            h.update(b)
            left -= len(b)
            if progress:
                progress("sprawdzam…", 1 - left / max(length, 1), "")
    return h


# ------------------------------------------------------------------ weryfikacja
def _first_diff(x: bytes, y: bytes) -> int:
    m = min(len(x), len(y))
    for i in range(m):
        if x[i] != y[i]:
            return i
    return m


def _mask(buf: bytes, off: int, ranges: List[list]) -> bytes:
    if not ranges or not overlaps(ranges, off, len(buf)):
        return buf
    b = bytearray(buf)
    for o, l in ranges:
        s, e = max(o, off), min(o + l, off + len(buf))
        if s < e:
            b[s - off:e - off] = b"\x00" * (e - s)
    return bytes(b)


def verify_image(source: str, size: int, image: str, *, sector_size: int = 512, chunk_size: int = 4 * 1048576,
                 progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Porównuje nośnik z obrazem bajt po bajcie. Znane (zapisane) nieczytelne zakresy są pomijane."""
    if not os.path.exists(image):
        raise ImagingError(f"Nie znaleziono obrazu: {image}")
    side = load_sidecar(image) or {}
    known_bad = side.get("bad_ranges", [])
    img_size = file_size(image)
    res: Dict[str, Any] = {"image": image, "device_size": size, "image_size": img_size,
                           "size_match": img_size == size, "compared_bytes": 0, "mismatched_chunks": 0,
                           "first_mismatch_offset": None, "device_read_errors": 0, "known_bad_bytes": sum(r[1] for r in known_bad),
                           "complete_image": bool(side.get("progress", {}).get("complete", True) if side else True)}
    algo = side.get("image", {}).get("hash_algorithm", "sha256")
    hd = hashlib.new(algo)
    new_bad: List[list] = []
    total = min(size, img_size or 0)
    chunk_size = max(sector_size, (chunk_size // sector_size) * sector_size)
    t0 = time.monotonic()
    with RawReader(source) as dev, open(image, "rb") as img:
        offset = 0
        while offset < total:
            if cancel():
                raise Cancelled("Weryfikacja przerwana")
            n = min(chunk_size, total - offset)
            a = dev.read_resilient(offset, n, sector_size, new_bad)
            img.seek(offset)
            b = img.read(n)
            hd.update(a)
            ma = _mask(a, offset, known_bad + new_bad)
            mb = _mask(b, offset, known_bad + new_bad)
            if ma != mb:
                res["mismatched_chunks"] += 1
                if res["first_mismatch_offset"] is None:
                    res["first_mismatch_offset"] = offset + _first_diff(ma, mb)
            offset += n
            res["compared_bytes"] = offset
            if progress:
                progress("weryfikacja", offset / max(total, 1), _speed(offset, t0))
    res["device_read_errors"] = sum(r[1] for r in new_bad) // sector_size
    res["unreadable_ranges"] = new_bad
    res["device_hash"] = hd.hexdigest() if size == total else None
    res["hash_algorithm"] = algo
    recorded = side.get("image", {}).get("hash")
    res["recorded_hash"] = recorded
    res["hash_matches_record"] = (res["device_hash"] == recorded) if (recorded and res["device_hash"]
                                                                       and not known_bad and not new_bad) else None
    res["match"] = bool(res["size_match"] and res["mismatched_chunks"] == 0 and res["complete_image"])
    return res


# ------------------------------------------------------------------ inspekcja / porównanie / hash
def inspect_image(image: str, *, compute_hash: bool = False, algo: Optional[str] = None,
                  progress: Progress = None) -> Dict[str, Any]:
    if not os.path.exists(image):
        raise ImagingError(f"Nie znaleziono obrazu: {image}")
    size = file_size(image) or 0
    side = load_sidecar(image)
    with open(image, "rb") as f:
        def read(o: int, n: int) -> bytes:
            f.seek(o)
            return f.read(n)
        table = inspect_table(read, size)
    res: Dict[str, Any] = {"image": os.path.abspath(image), "size_bytes": size, "metadata_present": side is not None,
                           "partition_table": table}
    if side:
        res["metadata"] = side
        res["complete"] = side.get("progress", {}).get("complete")
    else:
        res["complete"] = None
        res["note"] = "Brak pliku .mtinfo.json – obraz nie został utworzony przez media-tool lub plik pomocniczy usunięto"
    if compute_hash:
        a = algo or (side or {}).get("image", {}).get("hash_algorithm", "sha256")
        res["computed_hash"] = {"algorithm": a, "value": hash_file(image, a, progress)}
        rec = (side or {}).get("image", {}).get("hash")
        if rec and (side or {}).get("image", {}).get("hash_algorithm") == a:
            res["hash_matches_record"] = rec == res["computed_hash"]["value"]
    return res


def hash_file(path: str, algo: str = "sha256", progress: Progress = None,
              cancel: Callable[[], bool] = lambda: False) -> str:
    try:
        h = hashlib.new(algo)
    except ValueError:
        raise ImagingError(f"Nieznany algorytm: {algo}")
    total = file_size(path) or 1
    done = 0
    t0 = time.monotonic()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(4 * 1048576)
                if not b:
                    break
                if cancel():
                    raise Cancelled("Przerwano")
                h.update(b)
                done += len(b)
                if progress:
                    progress("suma kontr.", done / total, _speed(done, t0))
    except OSError as e:
        raise ImagingError(f"Nie można odczytać {path}: {e}")
    return h.hexdigest()


def compare_images(a: str, b: str, *, algo: str = "sha256", chunk_size: int = 4 * 1048576,
                   progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    for p in (a, b):
        if not os.path.exists(p):
            raise ImagingError(f"Nie znaleziono: {p}")
    sa, sb = file_size(a) or 0, file_size(b) or 0
    ha, hb = hashlib.new(algo), hashlib.new(algo)
    diff_chunks, first = 0, None
    longest = max(sa, sb)
    offset = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while offset < longest:
            if cancel():
                raise Cancelled("Przerwano")
            x, y = fa.read(chunk_size), fb.read(chunk_size)
            ha.update(x)
            hb.update(y)
            if x != y:
                diff_chunks += 1
                if first is None:
                    first = offset + _first_diff(x, y)
            offset += chunk_size
            if progress:
                progress("porównanie", min(offset, longest) / max(longest, 1), "")
    return {"a": a, "b": b, "size_a": sa, "size_b": sb, "identical": sa == sb and diff_chunks == 0,
            "different_chunks": diff_chunks, "first_difference_offset": first, "algorithm": algo,
            "hash_a": ha.hexdigest(), "hash_b": hb.hexdigest()}

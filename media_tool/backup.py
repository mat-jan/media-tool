"""Kopia plików z nośnika (backup przyrostowy z weryfikacją).

Zasady bezpieczeństwa:
* źródło (karta/dysk) jest tylko czytane – nic na nim nie jest zmieniane ani usuwane;
* kopia nigdy niczego nie nadpisuje: gdy plik o tej samej nazwie ma inną zawartość (np. aparat zaczął numerować
  zdjęcia od nowa po sformatowaniu karty), nowa wersja dostaje przyrostek `_<8 znaków SHA-256>`;
* każdy skopiowany plik jest czytany ponownie z miejsca docelowego i porównywany sumą SHA-256 ze źródłem;
* kolejne uruchomienia są przyrostowe (pliki bez zmian są pomijane bez czytania), a przerwane można po prostu
  uruchomić ponownie.

Układ w folderze docelowym:  <cel>/<nazwa>/…pliki…  oraz  <cel>/<nazwa>/.media-tool-backup/{index.json,runs/*.json}
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from . import __version__
from .errors import Cancelled, MediaToolError
from .util import atomic_write_json, chown_to_invoking_user, makedirs_owned

META_DIR = ".media-tool-backup"
INDEX_FILE = "index.json"
CHUNK = 1024 * 1024
SAVE_EVERY_FILES = 200

CATEGORIES = {
    "zdjęcia": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff", ".webp", ".raw", ".cr2", ".cr3",
                ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".srw", ".pef"},
    "filmy": {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".mxf", ".3gp", ".wmv", ".braw", ".lrv", ".thm"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".aif", ".aiff"},
    "dokumenty": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".odt", ".ods"},
}
SKIP_DIRS = {".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems", "System Volume Information",
             "$RECYCLE.BIN", ".DocumentRevisions-V100", ".VolumeIcon.icns", META_DIR, ".driveinfo_test"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".metadata_never_index"}
Progress = Optional[Callable[[str, float, str], None]]


def category(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "inne"


def sanitize_name(name: str, fallback: str = "nosnik") -> str:
    """Bezpieczna nazwa folderu (bez separatorów ścieżek i znaków zabronionych w nazwach plików)."""
    clean = re.sub(r"[^\w .\-+()]", "_", name or "", flags=re.UNICODE).strip(" ._")
    clean = re.sub(r"_{2,}", "_", clean)[:64].strip(" ._")
    return clean or fallback


def file_sha256(path: str, cancel: Callable[[], bool] = lambda: False) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            if cancel():
                raise Cancelled("Przerwano")
            h.update(b)
    return h.hexdigest()


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _is_within(child: str, parent: str) -> bool:
    c, p = os.path.realpath(child), os.path.realpath(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


# ------------------------------------------------------------------ indeks
class Index:
    """index.json: ścieżka względna -> lista wersji {size, mtime_ns, sha256, dest, backed_up_at}."""

    def __init__(self, meta_dir: str):
        self.path = os.path.join(meta_dir, INDEX_FILE)
        self.files: Dict[str, List[dict]] = {}
        self.created_at = _now()
        self._dirty = False
        try:
            import json
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.files = data.get("files", {})
            self.created_at = data.get("created_at", self.created_at)
        except (OSError, ValueError):
            pass

    def versions(self, rel: str) -> List[dict]:
        return self.files.get(rel, [])

    def add(self, rel: str, version: dict) -> None:
        self.files.setdefault(rel, []).append(version)
        self._dirty = True

    def touch(self) -> None:
        self._dirty = True

    def save(self, force: bool = False) -> None:
        if self._dirty or force:
            atomic_write_json(self.path, {"format": 1, "tool": "media-tool", "tool_version": __version__,
                                          "created_at": self.created_at, "updated_at": _now(), "files": self.files})
            self._dirty = False


# ------------------------------------------------------------------ skan źródła
def scan_source(root: str, only: Optional[Set[str]] = None) -> Tuple[List[Tuple[str, str, int, int]], List[str]]:
    """Zwraca ([(ścieżka, względna, rozmiar, mtime_ns)], błędy). Pomija łącza symboliczne i pliki systemowe."""
    files: List[Tuple[str, str, int, int]] = []
    errors: List[str] = []

    def onerror(e: OSError) -> None:
        errors.append(f"{e.filename}: {e.strerror or e}")

    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root, onerror=onerror):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            if fn in SKIP_FILES or fn.startswith("._"):
                continue
            p = os.path.join(dp, fn)
            if os.path.islink(p):
                continue
            if only and category(fn) not in only:
                continue
            try:
                st = os.stat(p)
            except OSError as e:
                errors.append(f"{p}: {e.strerror or e}")
                continue
            files.append((p, os.path.relpath(p, root).replace(os.sep, "/"), st.st_size, st.st_mtime_ns))
    return files, errors


def _dest_exists(target: str, version: dict) -> bool:
    p = os.path.join(target, *version["dest"].split("/"))
    try:
        return os.path.getsize(p) == version["size"]
    except OSError:
        return False


def _suffixed(rel: str, digest: str) -> str:
    stem, ext = os.path.splitext(rel)
    return f"{stem}_{digest[:8]}{ext}"


# ------------------------------------------------------------------ kopia
def check_paths(source: str, dest_root: str, refuse_same_volume: bool) -> List[str]:
    """Waliduje źródło/cel; zwraca ostrzeżenia, rzuca MediaToolError przy problemie blokującym."""
    warnings: List[str] = []
    if not os.path.isdir(source):
        raise MediaToolError(f"Źródło nie jest katalogiem (nośnik niezamontowany?): {source}")
    if _is_within(dest_root, source):
        raise MediaToolError("Folder docelowy nie może leżeć na kopiowanym nośniku/w kopiowanym folderze")
    probe = os.path.abspath(dest_root)
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    try:
        same = os.stat(source).st_dev == os.stat(probe).st_dev
    except OSError:
        same = False
    if same:
        msg = "Cel leży na tym samym woluminie co źródło – to nie jest prawdziwa kopia zapasowa"
        if refuse_same_volume:
            raise MediaToolError(msg)
        warnings.append(msg)
    return warnings


def run_backup(source: str, dest_root: str, name: str, *, only: Optional[Set[str]] = None, verify: bool = True,
               dry_run: bool = False, refuse_same_volume: bool = False, source_meta: Optional[Dict[str, Any]] = None,
               progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    name = sanitize_name(name)
    warnings = check_paths(source, dest_root, refuse_same_volume)
    target = os.path.join(dest_root, name)
    meta_dir = os.path.join(target, META_DIR)
    t0 = time.perf_counter()
    started = _now()

    files, scan_errors = scan_source(source, only)
    total_bytes = sum(f[2] for f in files)
    res: Dict[str, Any] = {
        "target": target, "name": name, "source": os.path.abspath(source), "dry_run": dry_run, "verify": verify,
        "scanned_files": len(files), "scanned_bytes": total_bytes, "copied": 0, "copied_bytes": 0,
        "skipped_unchanged": 0, "deduplicated": 0, "renamed_collisions": 0, "failed": [], "scan_errors": scan_errors,
        "warnings": warnings, "categories": sorted(only) if only else "wszystkie", "complete": False,
    }

    index = Index(meta_dir)
    if dry_run:
        for _, rel, size, mt in files:
            if any(v["size"] == size and v["mtime_ns"] == mt and _dest_exists(target, v) for v in index.versions(rel)):
                res["skipped_unchanged"] += 1
            else:
                res["copied"] += 1
                res["copied_bytes"] += size
        res["complete"] = True
        return res

    to_copy_estimate = 0
    for _, rel, size, mt in files:
        if not any(v["size"] == size and v["mtime_ns"] == mt and _dest_exists(target, v) for v in index.versions(rel)):
            to_copy_estimate += size
    if os.path.isdir(dest_root) or not os.path.exists(dest_root):
        probe = dest_root if os.path.isdir(dest_root) else os.path.dirname(os.path.abspath(dest_root))
        while probe and not os.path.exists(probe):
            probe = os.path.dirname(probe)
        free = shutil.disk_usage(probe).free
        if free < to_copy_estimate + 64 * 1024 * 1024:
            raise MediaToolError(f"Za mało miejsca w miejscu docelowym: potrzeba ~{to_copy_estimate} B, wolne {free} B")

    makedirs_owned(os.path.join(meta_dir, "tmp"))
    for leftover in Path(meta_dir, "tmp").glob("*.part"):      # resztki po przerwanym uruchomieniu
        try:
            leftover.unlink()
        except OSError:
            pass

    done_bytes = 0
    since_save = 0
    try:
        for src, rel, size, mt in files:
            if cancel():
                raise Cancelled("Przerwano – uruchom ponownie, aby kontynuować (pliki już skopiowane zostaną pominięte)")
            versions = index.versions(rel)
            if any(v["size"] == size and v["mtime_ns"] == mt and _dest_exists(target, v) for v in versions):
                res["skipped_unchanged"] += 1
            else:
                _copy_one(src, rel, size, mt, target, meta_dir, index, versions, verify, res, cancel)
                since_save += 1
                if since_save >= SAVE_EVERY_FILES:
                    index.save()
                    since_save = 0
            done_bytes += size
            if progress:
                el = max(time.perf_counter() - t0, 1e-6)
                progress("backup", done_bytes / max(total_bytes, 1),
                         f"{res['copied']} nowych, {res['skipped_unchanged']} bez zmian  {res['copied_bytes'] / 1048576 / el:6.1f} MB/s")
        res["complete"] = True
    finally:
        index.save()
        res["duration_s"] = round(time.perf_counter() - t0, 2)
        res["started_at"], res["finished_at"] = started, _now()
        _write_run_manifest(meta_dir, res, source_meta)
    return res


def _copy_one(src: str, rel: str, size: int, mtime_ns: int, target: str, meta_dir: str, index: Index,
              versions: List[dict], verify: bool, res: Dict[str, Any], cancel: Callable[[], bool]) -> None:
    tmp = os.path.join(meta_dir, "tmp", f"{uuid.uuid4().hex}.part")
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                b = fi.read(CHUNK)
                if not b:
                    break
                if cancel():
                    raise Cancelled("Przerwano")
                h.update(b)
                fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
    except Cancelled:
        _rm(tmp)
        raise
    except OSError as e:
        _rm(tmp)
        res["failed"].append([rel, f"odczyt/zapis: {e.strerror or e}"])
        return
    digest = h.hexdigest()

    # ta sama zawartość już zapisana pod tą nazwą (zmieniła się tylko data) -> nic nie kopiujemy
    same = next((v for v in versions if v["sha256"] == digest and _dest_exists(target, v)), None)
    if same:
        _rm(tmp)
        index.add(rel, {**same, "size": size, "mtime_ns": mtime_ns})
        res["deduplicated"] += 1
        return

    dest_rel = rel
    dest_abs = os.path.join(target, *dest_rel.split("/"))
    if os.path.lexists(dest_abs):
        if _same_file_content(dest_abs, digest):
            _rm(tmp)
            index.add(rel, {"size": size, "mtime_ns": mtime_ns, "sha256": digest, "dest": dest_rel, "backed_up_at": _now()})
            res["deduplicated"] += 1
            return
        dest_rel = _suffixed(rel, digest)
        dest_abs = os.path.join(target, *dest_rel.split("/"))
        if os.path.lexists(dest_abs):
            if _same_file_content(dest_abs, digest):
                _rm(tmp)
                index.add(rel, {"size": size, "mtime_ns": mtime_ns, "sha256": digest, "dest": dest_rel, "backed_up_at": _now()})
                res["deduplicated"] += 1
                return
            dest_rel = _suffixed(rel, digest + uuid.uuid4().hex)     # skrajnie mało prawdopodobne
            dest_abs = os.path.join(target, *dest_rel.split("/"))
        res["renamed_collisions"] += 1

    try:
        makedirs_owned(os.path.dirname(dest_abs))
        os.replace(tmp, dest_abs)
        os.utime(dest_abs, ns=(mtime_ns, mtime_ns))
        chown_to_invoking_user(dest_abs)
    except OSError as e:
        _rm(tmp)
        res["failed"].append([rel, f"zapis w miejscu docelowym: {e.strerror or e}"])
        return
    if verify:
        try:
            ok = file_sha256(dest_abs) == digest
        except OSError:
            ok = False
        if not ok:
            _rm(dest_abs)
            res["failed"].append([rel, "weryfikacja po zapisie nie powiodła się (kopia usunięta)"])
            return
    index.add(rel, {"size": size, "mtime_ns": mtime_ns, "sha256": digest, "dest": dest_rel, "backed_up_at": _now()})
    res["copied"] += 1
    res["copied_bytes"] += size


def _same_file_content(path: str, digest: str) -> bool:
    try:
        return os.path.isfile(path) and file_sha256(path) == digest
    except OSError:
        return False


def _rm(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_run_manifest(meta_dir: str, res: Dict[str, Any], source_meta: Optional[Dict[str, Any]]) -> None:
    run = {k: v for k, v in res.items() if k not in ("failed", "scan_errors")}
    run.update({"tool_version": __version__, "source_device": source_meta or {},
                "failed_count": len(res["failed"]), "failed": res["failed"][:1000],
                "scan_error_count": len(res["scan_errors"]), "scan_errors": res["scan_errors"][:200]})
    name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    try:
        atomic_write_json(os.path.join(meta_dir, "runs", name), run)
    except OSError:
        pass


# ------------------------------------------------------------------ weryfikacja i lista
def is_backup_set(path: str) -> bool:
    return os.path.isfile(os.path.join(path, META_DIR, INDEX_FILE))


def verify_backup(target: str, progress: Progress = None,
                  cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Sprawdza, czy wszystkie pliki z indeksu istnieją i mają zgodną sumę SHA-256."""
    if not is_backup_set(target):
        raise MediaToolError(f"To nie jest folder z kopią media-tool (brak {META_DIR}/{INDEX_FILE}): {target}")
    index = Index(os.path.join(target, META_DIR))
    entries = [(rel, v) for rel, vs in index.files.items() for v in vs]
    total = max(sum(v["size"] for _, v in entries), 1)
    done = 0
    ok = 0
    missing: List[str] = []
    corrupt: List[str] = []
    seen: Set[str] = set()
    t0 = time.perf_counter()
    for rel, v in entries:
        if cancel():
            raise Cancelled("Przerwano")
        seen.add(v["dest"])
        p = os.path.join(target, *v["dest"].split("/"))
        if not os.path.isfile(p):
            missing.append(v["dest"])
        else:
            try:
                good = os.path.getsize(p) == v["size"] and file_sha256(p, cancel) == v["sha256"]
            except OSError:
                good = False
            if good:
                ok += 1
            else:
                corrupt.append(v["dest"])
        done += v["size"]
        if progress:
            progress("weryfikacja", done / total, f"{done / 1048576 / max(time.perf_counter() - t0, 1e-6):6.1f} MB/s")
    extra: List[str] = []
    for dp, dns, fns in os.walk(target):
        dns[:] = [d for d in dns if d != META_DIR]
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), target).replace(os.sep, "/")
            if rel not in seen:
                extra.append(rel)
    return {"target": target, "versions_checked": len(entries), "ok": ok, "missing": missing, "corrupt": corrupt,
            "extra_files": sorted(extra)[:500], "extra_count": len(extra), "healthy": not missing and not corrupt}


def list_backups(path: str) -> Dict[str, Any]:
    """Jeśli `path` to zestaw kopii – lista uruchomień; jeśli folder z zestawami – lista zestawów."""
    import json

    def runs_of(setdir: str) -> List[Dict[str, Any]]:
        out = []
        rdir = Path(setdir, META_DIR, "runs")
        for f in sorted(rdir.glob("*.json")) if rdir.is_dir() else []:
            try:
                with open(f, encoding="utf-8") as fh:
                    r = json.load(fh)
                out.append({"file": f.name, "started_at": r.get("started_at"), "copied": r.get("copied"),
                            "copied_bytes": r.get("copied_bytes"), "skipped_unchanged": r.get("skipped_unchanged"),
                            "failed": r.get("failed_count", len(r.get("failed", []))), "complete": r.get("complete"),
                            "source": (r.get("source_device") or {}).get("model") or r.get("source")})
            except (OSError, ValueError):
                continue
        return out

    if is_backup_set(path):
        idx = Index(os.path.join(path, META_DIR))
        return {"path": path, "kind": "set", "files": len(idx.files),
                "versions": sum(len(v) for v in idx.files.values()),
                "bytes": sum(v["size"] for vs in idx.files.values() for v in vs), "runs": runs_of(path)}
    sets = []
    if os.path.isdir(path):
        for child in sorted(os.listdir(path)):
            cp = os.path.join(path, child)
            if is_backup_set(cp):
                runs = runs_of(cp)
                idx = Index(os.path.join(cp, META_DIR))
                sets.append({"name": child, "path": cp, "files": len(idx.files), "runs": len(runs),
                             "last_run": runs[-1]["started_at"] if runs else None,
                             "bytes": sum(v["size"] for vs in idx.files.values() for v in vs)})
    return {"path": path, "kind": "root", "sets": sets}

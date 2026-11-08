"""Test prędkości odczytu (tylko do odczytu)."""
from __future__ import annotations

import os
import random
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

from .errors import Cancelled, RawAccessError
from .rawio import RawReader
from .util import disable_cache

MIB = 1048576
Progress = Optional[Callable[[str, float, str], None]]


def _rate(nbytes: float, seconds: float) -> float:
    """MB/s; zegar bywa zbyt gruby dla krótkich odczytów (Windows) – nigdy nie dzielimy przez 0."""
    return round(nbytes / MIB / max(seconds, 1e-6), 1)


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def benchmark_raw(path: str, size: int, sector: int = 512, mib: int = 256, random_ops: int = 500,
                  progress: Progress = None, cancel: Callable[[], bool] = lambda: False,
                  seed: int = 1) -> Dict[str, Any]:
    """Odczyt sekwencyjny z kilku miejsc nośnika + losowy odczyt 4 KiB."""
    if size < 2 * MIB:
        raise RawAccessError("Nośnik zbyt mały do testu prędkości")
    sector = max(sector, 1)
    mib = max(4, min(mib, size // MIB))
    regions = 4
    per = max(1, mib // regions)
    positions = [0, size // 3, 2 * size // 3, size - per * MIB]
    seq: List[Dict[str, Any]] = []
    errors = 0
    with RawReader(path) as rd:
        for i, pos in enumerate(positions):
            pos = max(0, (pos // sector) * sector)
            t0 = time.perf_counter()
            got = 0
            try:
                for j in range(per):
                    if cancel():
                        raise Cancelled("Przerwano")
                    data = rd.read_at(pos + j * MIB, MIB)
                    got += len(data)
                    if len(data) < MIB:
                        break
            except OSError:
                errors += 1
            el = time.perf_counter() - t0
            seq.append({"offset": pos, "bytes": got, "mb_s": _rate(got, el) if got else 0.0})
            if progress:
                progress("odczyt sekw.", (i + 1) / (regions + 1), "")
        # losowy odczyt 4 KiB
        rnd = random.Random(seed)
        blk = max(4096, sector)
        span = max(1, (size - blk) // blk)
        lat: List[float] = []
        deadline = time.perf_counter() + 15
        for i in range(random_ops):
            if cancel():
                raise Cancelled("Przerwano")
            if time.perf_counter() > deadline:
                break
            off = rnd.randrange(span) * blk
            t = time.perf_counter()
            try:
                rd.read_at(off, blk)
            except OSError:
                errors += 1
                continue
            lat.append((time.perf_counter() - t) * 1000)
        if progress:
            progress("odczyt los.", 1.0, "")
    total_t = sum(l / 1000 for l in lat)
    speeds = [s["mb_s"] for s in seq if s["mb_s"] > 0]
    return {"mode": "urządzenie surowe", "path": path, "sequential": seq,
            "seq_avg_mb_s": round(statistics.mean(speeds), 1) if speeds else 0.0,
            "seq_min_mb_s": min(speeds) if speeds else 0.0, "seq_max_mb_s": max(speeds) if speeds else 0.0,
            "random_4k_ops": len(lat), "random_4k_iops": round(len(lat) / total_t) if total_t > 0 else 0,
            "latency_avg_ms": round(statistics.mean(lat), 3) if lat else 0.0,
            "latency_p95_ms": round(_percentile(lat, 0.95), 3) if lat else 0.0,
            "read_errors": errors,
            "note": "Odczyt omija pamięć podręczną systemu w miarę możliwości; wynik orientacyjny."}


def benchmark_files(mounts: List[str], mib: int = 256, random_ops: int = 300,
                    progress: Progress = None, cancel: Callable[[], bool] = lambda: False,
                    seed: int = 1) -> Dict[str, Any]:
    """Wariant bez uprawnień administratora: czyta istniejące pliki z zamontowanych woluminów."""
    files = []
    walk_deadline = time.perf_counter() + 8
    for m in mounts:
        for dp, dns, fns in os.walk(m, onerror=lambda e: None):
            if time.perf_counter() > walk_deadline:
                break
            dns[:] = [d for d in dns if not d.startswith(".")]
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    if not os.path.islink(p) and os.path.getsize(p) >= MIB:
                        files.append((os.path.getsize(p), p))
                except OSError:
                    pass
            if len(files) > 2000:
                break
    if not files:
        raise RawAccessError("Brak plików ≥ 1 MiB na zamontowanych woluminach – nie ma czego czytać "
                             "(uruchom jako administrator, aby testować surowe urządzenie)")
    files.sort(reverse=True)
    budget = mib * MIB
    got = 0
    t0 = time.perf_counter()
    errors = 0
    used = []
    for size, p in files:
        if got >= budget:
            break
        try:
            with open(p, "rb", buffering=0) as f:
                disable_cache(f.fileno())
                while got < budget:
                    if cancel():
                        raise Cancelled("Przerwano")
                    b = f.read(MIB)
                    if not b:
                        break
                    got += len(b)
                used.append(p)
        except OSError:
            errors += 1
        if progress:
            progress("odczyt plików", min(got / budget, 1.0), "")
    el = time.perf_counter() - t0
    rnd = random.Random(seed)
    lat: List[float] = []
    for _ in range(random_ops):
        size, p = files[rnd.randrange(min(len(files), 20))]
        try:
            with open(p, "rb", buffering=0) as f:
                disable_cache(f.fileno())
                f.seek(rnd.randrange(max(1, size - 4096)))
                t = time.perf_counter()
                f.read(4096)
                lat.append((time.perf_counter() - t) * 1000)
        except OSError:
            errors += 1
    total_t = sum(l / 1000 for l in lat)
    return {"mode": "pliki z woluminu", "files_used": len(used), "bytes_read": got,
            "seq_avg_mb_s": _rate(got, el) if got else 0.0,
            "random_4k_ops": len(lat), "random_4k_iops": round(len(lat) / total_t) if total_t > 0 else 0,
            "latency_avg_ms": round(statistics.mean(lat), 3) if lat else 0.0,
            "latency_p95_ms": round(_percentile(lat, 0.95), 3) if lat else 0.0, "read_errors": errors,
            "note": "Tryb awaryjny: wynik zależy od systemu plików i pamięci podręcznej; "
                    "dla dokładniejszego pomiaru uruchom z uprawnieniami administratora."}

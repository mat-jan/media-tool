"""Test integralności wybranych obszarów oraz skanowanie nośnika (tylko do odczytu)."""
from __future__ import annotations

import hashlib
import random
import re
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import Cancelled, MediaToolError
from .partition_tables import inspect_table
from .rawio import RawReader, add_range

MIB = 1048576
Progress = Optional[Callable[[str, float, str], None]]


def parse_size(text: str) -> int:
    """'4096', '512K', '1M', '2G' (też KiB/MiB/GiB) -> bajty."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)(?:i?B)?\s*", text, re.I)
    if not m:
        raise MediaToolError(f"Niepoprawny rozmiar: '{text}' (np. 4096, 1M, 512K)")
    mult = {"": 1, "K": 1024, "M": MIB, "G": 1024 ** 3, "T": 1024 ** 4}[m.group(2).upper()]
    return int(m.group(1)) * mult


def parse_range(text: str) -> Tuple[int, int]:
    """'START:LENGTH', np. '0:1M' lub '4096:512K'."""
    a, sep, b = text.partition(":")
    if not sep:
        raise MediaToolError(f"Zakres '{text}' musi mieć postać START:DŁUGOŚĆ")
    return parse_size(a), parse_size(b)


def default_areas(size: int, sector: int, samples: int, area_len: int, seed: int) -> List[Tuple[int, int]]:
    area_len = min(area_len, size)
    areas = [(0, area_len), (max(0, size // 2 - area_len // 2), area_len), (max(0, size - area_len), area_len)]
    rnd = random.Random(seed)
    span = max(1, (size - area_len) // sector)
    for _ in range(samples):
        areas.append((rnd.randrange(span) * sector, area_len))
    seen, out = set(), []
    for o, l in areas:
        o = (o // sector) * sector
        if o not in seen:
            seen.add(o)
            out.append((o, l))
    return out


def integrity(path: str, size: int, sector: int = 512, areas: Optional[List[Tuple[int, int]]] = None,
              samples: int = 8, area_len: int = MIB, passes: int = 2, seed: Optional[int] = None,
              progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Czyta wskazane obszary kilka razy i porównuje sumy – wykrywa błędy odczytu i niestabilne odczyty."""
    seed = int(time.time()) if seed is None else seed
    custom = areas is not None
    areas = areas or default_areas(size, sector, samples, area_len, seed)
    areas = [((o // sector) * sector, -(-(l + (o % sector)) // sector) * sector) for o, l in areas]  # do sektorów
    results: List[Dict[str, Any]] = []
    with RawReader(path) as rd:
        for i, (off, ln) in enumerate(areas):
            if off >= size:
                results.append({"offset": off, "length": ln, "error": "poza zakresem nośnika"})
                continue
            ln = min(ln, size - off)
            hashes, bad = [], []
            for _ in range(max(1, passes)):
                if cancel():
                    raise Cancelled("Przerwano")
                data = rd.read_resilient(off, ln, sector, bad)
                hashes.append(hashlib.sha256(data).hexdigest())
            results.append({"offset": off, "length": ln, "sha256": hashes[0],
                            "stable": len(set(hashes)) == 1,
                            "unreadable_bytes": sum(r[1] for r in bad), "unreadable_ranges": bad})
            if progress:
                progress("integralność", (i + 1) / len(areas), "")
        table = inspect_table(lambda o, n: rd.read_aligned(o, n, sector), size)
    ok = [r for r in results if "error" not in r]
    return {"areas": results, "areas_checked": len(ok), "seed": None if custom else seed, "passes": passes,
            "unstable_areas": sum(1 for r in ok if not r["stable"]),
            "areas_with_read_errors": sum(1 for r in ok if r["unreadable_bytes"]),
            "partition_table": table,
            "passed": all(r["stable"] and not r["unreadable_bytes"] for r in ok) and bool(ok)}


def scan(path: str, size: int, sector: int = 512, chunk_size: int = 4 * MIB, start: int = 0,
         length: Optional[int] = None, max_errors: int = 1000, segments: int = 100,
         progress: Progress = None, cancel: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Czyta cały (lub wskazany) zakres nośnika, zapisując nieczytelne sektory i wolne segmenty."""
    end = size if length is None else min(size, start + length)
    start = (start // sector) * sector
    chunk_size = max(sector, (chunk_size // sector) * sector)
    total = max(end - start, 1)
    seg_len = max(chunk_size, total // max(segments, 1))
    seg_speed: List[Tuple[int, float]] = []
    bad: List[list] = []
    aborted = None
    seg_start_off, seg_bytes, seg_t0 = start, 0, time.monotonic()
    t0 = time.monotonic()
    done = 0
    with RawReader(path) as rd:
        off = start
        while off < end:
            if cancel():
                raise Cancelled("Przerwano")
            n = min(chunk_size, end - off)
            data = rd.read_resilient(off, n, sector, bad)
            if len(data) < n:
                aborted = f"koniec danych na {off + len(data)} B"
                done += len(data)
                break
            off += n
            done += n
            seg_bytes += n
            if seg_bytes >= seg_len:
                el = time.monotonic() - seg_t0
                seg_speed.append((seg_start_off, seg_bytes / MIB / el if el > 0 else 0.0))
                seg_start_off, seg_bytes, seg_t0 = off, 0, time.monotonic()
            if sum(r[1] for r in bad) // sector > max_errors:
                aborted = f"przekroczono limit {max_errors} nieczytelnych sektorów"
                break
            if progress:
                el = time.monotonic() - t0
                progress("skanowanie", done / total, f"{done / MIB / el:7.1f} MB/s" if el > 0 else "")
    slow = []
    speeds = [s for _, s in seg_speed if s > 0]
    if len(speeds) >= 5:
        med = statistics.median(speeds)
        slow = [{"offset": o, "mb_s": round(s, 1)} for o, s in seg_speed if s < med * 0.25][:20]
    el = time.monotonic() - t0
    return {"range": [start, end], "bytes_read": done, "unreadable_ranges": bad,
            "unreadable_sectors": sum(r[1] for r in bad) // sector, "aborted": aborted,
            "avg_mb_s": round(done / MIB / el, 1) if el > 0 else 0.0,
            "median_segment_mb_s": round(statistics.median(speeds), 1) if speeds else None,
            "slow_segments": slow, "clean": not bad and not aborted and done >= end - start}

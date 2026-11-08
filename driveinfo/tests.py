"""Media tests: capacity (fake detection) and speed.

Both tests only write ordinary files into a hidden directory on the *mounted
volume* (never to the raw device) and always clean up after themselves.
"""
import os
import random
import shutil
import sys
import time
from pathlib import Path

MB = 1024 * 1024
BLOCK = 1 * MB            # unit of verification
FILE_BLOCKS = 128         # 128 MB per test file
TEST_DIR = ".driveinfo_test"


class Cancelled(Exception):
    pass


def _base_pattern():
    r = random.Random(0xD15C)
    return bytes(r.getrandbits(8) for _ in range(4096)) * (BLOCK // 4096)


_BASE = None


def block_data(index):
    """Deterministic, unique 1 MiB block for a global block index."""
    global _BASE
    if _BASE is None:
        _BASE = _base_pattern()
    return index.to_bytes(8, "little") + _BASE[8:]


def _nocache(fd):
    """Best-effort bypass of the OS page cache so reads hit the media."""
    try:
        if sys.platform == "darwin":
            import fcntl
            fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
        elif hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass


def check_target(path):
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"Not a mounted directory: {path}")
    if p.resolve() == Path("/") or str(p.resolve()) in ("/System/Volumes/Data",):
        raise ValueError("Refusing to run on the system volume")
    if not os.access(p, os.W_OK):
        raise ValueError(f"Not writable: {path}")
    return p


def _noop(*_):
    pass


def capacity_test(mount, limit_mb=None, progress=_noop, cancel=lambda: False):
    """Fill free space with verifiable data, read it back and compare.

    progress(phase, done_fraction, mb_per_s). Returns a result dict.
    """
    root = check_target(mount)
    work = root / TEST_DIR
    work.mkdir(exist_ok=True)
    free = shutil.disk_usage(root).free
    total_blocks = max(0, (free - 8 * MB) // BLOCK)   # keep a small margin
    if limit_mb:
        total_blocks = min(total_blocks, int(limit_mb))
    if total_blocks < 1:
        shutil.rmtree(work, ignore_errors=True)
        raise ValueError("Not enough free space to test")
    files = []
    result = {"test": "capacity", "tested_mb": 0, "errors": 0, "first_bad_mb": None,
              "write_mb_s": 0.0, "read_mb_s": 0.0, "verdict": "", "note": ""}
    try:
        # ---- write phase
        t0, written = time.monotonic(), 0
        try:
            idx = 0
            while idx < total_blocks:
                n = min(FILE_BLOCKS, total_blocks - idx)
                f = work / f"t{idx:08d}.bin"
                files.append((f, idx, n))
                with open(f, "wb") as fh:
                    for i in range(n):
                        if cancel():
                            raise Cancelled()
                        fh.write(block_data(idx + i))
                        written += 1
                        if written % 8 == 0:
                            el = time.monotonic() - t0
                            progress("write", written / total_blocks, written / el if el else 0)
                    fh.flush()
                    os.fsync(fh.fileno())
                idx += n
        except OSError as e:
            result["errors"] += 1
            result["note"] = f"Write failed after {written} MB: {e}"
            total_blocks = written
        el = time.monotonic() - t0
        result["write_mb_s"] = round(written / el, 1) if el else 0.0
        result["tested_mb"] = written

        # ---- verify phase
        t0, read, bad = time.monotonic(), 0, 0
        for f, start, n in files:
            if not f.exists():
                continue
            try:
                with open(f, "rb", buffering=0) as fh:
                    _nocache(fh.fileno())
                    for i in range(n):
                        if cancel():
                            raise Cancelled()
                        data = fh.read(BLOCK)
                        if data != block_data(start + i):
                            bad += 1
                            if result["first_bad_mb"] is None:
                                result["first_bad_mb"] = start + i
                        read += 1
                        if read % 8 == 0:
                            el = time.monotonic() - t0
                            progress("verify", read / max(total_blocks, 1), read / el if el else 0)
            except OSError as e:
                bad += 1
                result["note"] = f"Read failed: {e}"
        el = time.monotonic() - t0
        result["read_mb_s"] = round(read / el, 1) if el else 0.0
        result["errors"] += bad
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if result["errors"]:
        result["verdict"] = "FAIL - data mismatch / write errors: likely fake or damaged"
    else:
        result["verdict"] = "PASS - all tested data read back correctly"
        if limit_mb and result["tested_mb"] < free // MB - 16:
            result["note"] = (result["note"] + " Partial test: only the first "
                              f"{result['tested_mb']} MB of free space was checked.").strip()
    return result


def classify(seq_write, rand_read_iops, rand_write_iops):
    """Map measured values to SD speed classes (indicative only)."""
    cls = "below Class 2"
    for floor, name in ((30, "V30 / U3"), (10, "V10 / U1 / Class 10"),
                        (6, "Class 6"), (4, "Class 4"), (2, "Class 2")):
        if seq_write >= floor:
            cls = name
            break
    app = ""
    if rand_read_iops >= 4000 and rand_write_iops >= 2000:
        app = "A2"
    elif rand_read_iops >= 1500 and rand_write_iops >= 500:
        app = "A1"
    return cls, app


def speed_test(mount, size_mb=256, progress=_noop, cancel=lambda: False):
    """Sequential write/read + random 4K read/write on a temporary file."""
    root = check_target(mount)
    free = shutil.disk_usage(root).free // MB
    size_mb = int(min(size_mb, max(free - 16, 0)))
    if size_mb < 16:
        raise ValueError("Not enough free space for the speed test")
    work = root / TEST_DIR
    work.mkdir(exist_ok=True)
    f = work / "speed.bin"
    try:
        # sequential write
        t0 = time.monotonic()
        with open(f, "wb") as fh:
            for i in range(size_mb):
                if cancel():
                    raise Cancelled()
                fh.write(block_data(i))
                if i % 8 == 0:
                    progress("seq write", i / size_mb / 4, 0)
            fh.flush()
            os.fsync(fh.fileno())
        seq_w = size_mb / (time.monotonic() - t0)

        # sequential read
        t0 = time.monotonic()
        with open(f, "rb", buffering=0) as fh:
            _nocache(fh.fileno())
            for i in range(size_mb):
                if cancel():
                    raise Cancelled()
                fh.read(BLOCK)
                if i % 8 == 0:
                    progress("seq read", 0.25 + i / size_mb / 4, 0)
        seq_r = size_mb / (time.monotonic() - t0)

        # random 4K read / write
        ops = 500
        rnd = random.Random(1)
        span = size_mb * MB // 4096
        fd = os.open(f, os.O_RDWR)
        try:
            _nocache(fd)
            t0 = time.monotonic()
            for i in range(ops):
                if cancel():
                    raise Cancelled()
                os.pread(fd, 4096, rnd.randrange(span) * 4096)
            r_iops = ops / (time.monotonic() - t0)
            progress("rand read", 0.75, 0)
            buf = os.urandom(4096)
            t0 = time.monotonic()
            for i in range(ops):
                if cancel():
                    raise Cancelled()
                os.pwrite(fd, buf, rnd.randrange(span) * 4096)
                os.fsync(fd)
            w_iops = ops / (time.monotonic() - t0)
        finally:
            os.close(fd)
        progress("done", 1.0, 0)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    cls, app = classify(seq_w, r_iops, w_iops)
    return {"test": "speed", "size_mb": size_mb,
            "seq_write_mb_s": round(seq_w, 1), "seq_read_mb_s": round(seq_r, 1),
            "rand_read_iops": round(r_iops), "rand_write_iops": round(w_iops),
            "speed_class": cls, "app_class": app,
            "note": "Measured through the filesystem; indicative, not a certification."}

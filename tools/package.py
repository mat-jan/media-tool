#!/usr/bin/env python3
"""Pakuje paczkę do pobrania: python3 tools/package.py NAZWA PLIK [PLIK ...]

Wszystkie pliki trafiają do katalogu `media-tool/` w archiwum, z zachowaniem bitu wykonywalnego
(ważne dla Start-*.sh / .command na macOS i Linuksie) oraz README i LICENSE."""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRA = ["README.md"]


def package(name: str, files: list, out_dir: str = None) -> str:
    out_dir = out_dir or os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"media-tool-{name}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files + [os.path.join(ROOT, e) for e in EXTRA]:
            if not os.path.exists(f):
                raise SystemExit(f"Brak pliku: {f}")
            info = zipfile.ZipInfo("media-tool/" + os.path.basename(f))
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = os.stat(f).st_mode & 0o777
            if os.path.basename(f).endswith((".sh", ".command")) or os.path.basename(f) in (
                    "media-tool", "media-tool.pyz"):
                mode |= 0o755
            info.external_attr = (0o100000 | mode) << 16
            info.date_time = (2026, 1, 1, 0, 0, 0)
            with open(f, "rb") as fh:
                z.writestr(info, fh.read())
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    print(package(sys.argv[1], sys.argv[2:]))

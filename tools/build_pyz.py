#!/usr/bin/env python3
"""Buduje dist/media-tool.pyz – jeden plik uruchamiany samym Pythonem 3.9+ (bez instalacji pakietów):

    python3 media-tool.pyz            # menu
    python3 media-tool.pyz list
"""
import os
import shutil
import sys
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = "import sys\nfrom media_tool.cli import main\nsys.exit(main())\n"


def build(out: str = None) -> str:
    out = out or os.path.join(ROOT, "dist", "media-tool.pyz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory() as stage:
        shutil.copytree(os.path.join(ROOT, "media_tool"), os.path.join(stage, "media_tool"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with open(os.path.join(stage, "__main__.py"), "w", encoding="utf-8") as f:
            f.write(MAIN)
        zipapp.create_archive(stage, out, interpreter="/usr/bin/env python3", compressed=True)
    os.chmod(out, 0o755)
    return out


if __name__ == "__main__":
    path = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"{path} ({os.path.getsize(path) // 1024} KiB)")

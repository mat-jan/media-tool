"""Punkt wejścia dla PyInstallera (importy bezwzględne; media_tool/__main__.py używa względnych)."""
import sys

from media_tool.cli import main

if __name__ == "__main__":
    sys.exit(main())

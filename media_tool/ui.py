"""Warstwa wejścia/wyjścia terminala (wstrzykiwalna w testach)."""
from __future__ import annotations

import sys
import time
from typing import Callable, List, Optional, Sequence


class UserAbort(Exception):
    """Użytkownik przerwał (Ctrl+C / EOF) lub odmówił."""


class Console:
    def __init__(self, input_fn: Optional[Callable[[str], str]] = None, out=None, err=None,
                 interactive: Optional[bool] = None, assume_yes: bool = False):
        self._input = input_fn or input
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        if interactive is None:
            interactive = bool(getattr(sys.stdin, "isatty", lambda: False)()) and input_fn is None
        self.interactive = interactive if input_fn is None else True
        self.assume_yes = assume_yes
        self._last_progress = 0.0

    # ---- wyjście
    def line(self, msg: str = "") -> None:
        print(msg, file=self.out)

    def info(self, msg: str) -> None:
        print(msg, file=self.err)

    def warn(self, msg: str) -> None:
        print(f"UWAGA: {msg}", file=self.err)

    def error(self, msg: str) -> None:
        print(f"BŁĄD: {msg}", file=self.err)

    # ---- wejście
    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            raw = self._input(f"{prompt}{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            raise UserAbort()
        raw = raw.strip()
        return raw or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        ans = self.ask(f"{prompt} (t/n)", "t" if default else "n").lower()
        return ans in ("t", "tak", "y", "yes")

    def choose(self, title: str, options: Sequence[str], default: Optional[int] = None) -> int:
        """Menu jednokrotnego wyboru; zwraca indeks (0-based)."""
        self.line(f"\n{title}")
        for i, o in enumerate(options, 1):
            self.line(f"  {i:>2}) {o}")
        while True:
            raw = self.ask("Wybór", str(default + 1) if default is not None else "")
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self.error(f"Podaj numer 1-{len(options)}.")

    # ---- postęp
    def progress(self, label: str, frac: float, extra: str = "") -> None:
        if not getattr(self.err, "isatty", lambda: False)():
            return
        now = time.monotonic()
        if frac < 1.0 and now - self._last_progress < 0.15:
            return
        self._last_progress = now
        bar_w = 28
        filled = int(bar_w * max(0.0, min(frac, 1.0)))
        print(f"\r{label:<14} [{'#' * filled}{'.' * (bar_w - filled)}] {frac * 100:5.1f}% {extra}   ",
              end="", file=self.err, flush=True)
        if frac >= 1.0:
            print(file=self.err)

    # ---- tabela tekstowa
    def table(self, headers: List[str], rows: List[List[str]]) -> None:
        for ln in format_table(headers, rows):
            self.line(ln)


def format_table(headers: List[str], rows: List[List[str]]) -> List[str]:
    widths = [max([len(h)] + [len(str(r[i])) for r in rows]) for i, h in enumerate(headers)]

    def fmt(r):
        return "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip()
    return [fmt(headers), fmt(["-" * w for w in widths])] + [fmt(r) for r in rows]

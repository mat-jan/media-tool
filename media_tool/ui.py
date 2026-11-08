"""Warstwa wejścia/wyjścia terminala: kolory, ramki, tabele, pasek postępu (tylko biblioteka standardowa).

Kolory włączają się tylko w prawdziwym terminalu; wyłącza je `NO_COLOR=1`, wymusza `MEDIA_TOOL_COLOR=1`.
`MEDIA_TOOL_ASCII=1` zamienia ramki i symbole na zwykłe znaki ASCII.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Callable, List, Optional, Sequence


class UserAbort(Exception):
    """Użytkownik przerwał (Ctrl+C / EOF) lub odmówił."""


# ------------------------------------------------------------------ kolory
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34",
          "magenta": "35", "cyan": "36", "gray": "90"}
_enabled = False


def _ascii_mode() -> bool:
    return os.environ.get("MEDIA_TOOL_ASCII") == "1"


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def visible_len(s: str) -> int:
    return len(strip_ansi(s))


def color_enabled() -> bool:
    return _enabled


def set_color(value: bool) -> None:
    global _enabled
    _enabled = value


def paint(text: str, *names: str) -> str:
    if not _enabled or not text:
        return text
    return "\x1b[" + ";".join(_CODES[n] for n in names) + "m" + text + "\x1b[0m"


def bold(s): return paint(s, "bold")
def dim(s): return paint(s, "dim")
def red(s): return paint(s, "red")
def green(s): return paint(s, "green")
def yellow(s): return paint(s, "yellow")
def cyan(s): return paint(s, "cyan")
def heading(s): return paint(s, "bold", "cyan")


def sym(name: str) -> str:
    uni = {"ok": "✔", "bad": "✖", "warn": "⚠", "info": "ℹ", "arrow": "›", "dot": "•", "partial": "◐"}
    asc = {"ok": "+", "bad": "x", "warn": "!", "info": "i", "arrow": ">", "dot": "*", "partial": "~"}
    return (asc if _ascii_mode() else uni)[name]


def good(s: str) -> str:
    return green(f"{sym('ok')} {s}")


def problem(s: str) -> str:
    return red(f"{sym('bad')} {s}")


def caution(s: str) -> str:
    return yellow(f"{sym('warn')} {s}")


def detect_color(stream) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("MEDIA_TOOL_COLOR") == "1":
        return True
    if not getattr(stream, "isatty", lambda: False)() or os.environ.get("TERM") == "dumb":
        return False
    if sys.platform == "win32":
        return _enable_windows_vt()
    return True


def _enable_windows_vt() -> bool:
    """Windows 10+: włącza interpretowanie sekwencji ANSI w konsoli."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        ok = False
        for std in (-11, -12):
            h = k.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                ok = bool(k.SetConsoleMode(h, mode.value | 0x0004)) or ok
        return ok
    except Exception:
        return False


def fmt_duration(seconds: float) -> str:
    s = int(max(seconds, 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# ------------------------------------------------------------------ tabele
def format_table(headers: List[str], rows: List[List[str]]) -> List[str]:
    """Wyrównanie według szerokości widocznej (kody kolorów nie psują układu)."""
    widths = [max([visible_len(h)] + [visible_len(str(r[i])) for r in rows]) for i, h in enumerate(headers)]

    def fmt(r, hdr=False):
        cells = []
        for v, w in zip(r, widths):
            v = str(v)
            cells.append(v + " " * (w - visible_len(v)))
        line = "  ".join(cells).rstrip()
        return bold(line) if hdr else line
    return [fmt(headers, True), dim(("  ").join("─" * w if not _ascii_mode() else "-" * w for w in widths))] + \
           [fmt(r) for r in rows]


def _wrap_lines(lines: List[str], maxw: int) -> List[str]:
    """Zawija długie linie bez kolorów (ostrzeżenia nie mogą być obcinane); linie z kolorami zostają bez zmian."""
    import textwrap
    out: List[str] = []
    for ln in lines:
        if "\x1b" in ln or len(ln) <= maxw:
            out.append(ln)
            continue
        indent = " " * (len(ln) - len(ln.lstrip(" ")))
        out.extend(textwrap.wrap(ln, width=maxw, subsequent_indent=indent + "  ", break_long_words=True) or [ln])
    return out


# ------------------------------------------------------------------ konsola
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
        self._prog: dict = {}
        set_color(detect_color(self.out))

    # ---- wyjście
    def line(self, msg: str = "") -> None:
        print(msg, file=self.out)

    def info(self, msg: str) -> None:
        print(msg, file=self.err)

    def warn(self, msg: str) -> None:
        print(caution(f"UWAGA: {msg}"), file=self.err)

    def error(self, msg: str) -> None:
        print(problem(f"BŁĄD: {msg}"), file=self.err)

    def ok(self, msg: str, stream: str = "out") -> None:
        print(good(msg), file=self.out if stream == "out" else self.err)

    def section(self, title: str, stream: str = "out") -> None:
        s = self.out if stream == "out" else self.err
        print(f"\n{heading(title)}", file=s)

    def box(self, title: str, lines: Sequence[str], kind: str = "info", stream: str = "err") -> None:
        """Ramka z tytułem; `kind`: info (cyjan), ok, warn, bad."""
        col = {"info": "cyan", "ok": "green", "warn": "yellow", "bad": "red"}[kind]
        tl, tr, bl, br, h, v = ("+", "+", "+", "+", "-", "|") if _ascii_mode() else ("╭", "╮", "╰", "╯", "─", "│")
        width = shutil.get_terminal_size((100, 24)).columns
        lines = _wrap_lines(list(lines), max(width - 4, 20))
        inner = min(max([visible_len(x) for x in lines] + [visible_len(title) + 2]), max(width - 4, 20))

        def fit(x: str) -> str:
            if visible_len(x) <= inner:
                return x + " " * (inner - visible_len(x))
            if "\x1b" in x:
                return x
            return x[: inner - 1] + "…" if not _ascii_mode() else x[: inner - 3] + "..."
        top = paint(f"{tl}{h} ", col) + paint(title, "bold", col) + paint(f" {h * max(inner - visible_len(title) - 1, 1)}{tr}", col)
        s = self.err if stream == "err" else self.out
        print(top, file=s)
        for x in lines:
            print(f"{paint(v, col)} {fit(x)} {paint(v, col)}", file=s)
        print(paint(f"{bl}{h * (inner + 2)}{br}", col), file=s)

    def banner(self, title: str, subtitle: str = "") -> None:
        bar = ("=" if _ascii_mode() else "━") * min(60, shutil.get_terminal_size((80, 24)).columns - 2)
        self.line(f"\n{cyan(bar)}\n {bold(title)}   {dim(subtitle)}\n{cyan(bar)}")

    # ---- wejście
    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            raw = self._input(f"{cyan(sym('arrow'))} {prompt}{dim(suffix)}: ")
        except (EOFError, KeyboardInterrupt):
            raise UserAbort()
        raw = raw.strip()
        return raw or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        ans = self.ask(f"{prompt} {dim('(t/n)')}", "t" if default else "n").lower()
        return ans in ("t", "tak", "y", "yes")

    def choose(self, title: str, options: Sequence[str], default: Optional[int] = None) -> int:
        """Menu jednokrotnego wyboru; zwraca indeks (0-based)."""
        self.line(f"\n{heading(title)}")
        for i, o in enumerate(options, 1):
            self.line(f"  {cyan(f'{i:>2}')}  {o}")
        while True:
            raw = self.ask("Wybór", str(default + 1) if default is not None else "")
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self.error(f"Podaj numer 1-{len(options)}.")

    # ---- postęp
    def progress(self, label: str, frac: float, extra: str = "") -> None:
        if not getattr(self.err, "isatty", lambda: False)():
            return
        now = time.perf_counter()
        st = self._prog
        if st.get("label") != label or frac < st.get("last", 0.0):
            st = self._prog = {"label": label, "t0": now, "last": frac}
        st["last"] = frac
        if frac < 1.0 and now - self._last_progress < 0.12:
            return
        self._last_progress = now
        frac = max(0.0, min(frac, 1.0))
        eta = ""
        elapsed = now - st["t0"]
        if 0.02 < frac < 1.0 and elapsed > 1.0:
            eta = f"ETA {fmt_duration(elapsed * (1 - frac) / frac)}"
        full, empty = ("#", ".") if _ascii_mode() else ("█", "░")
        bar_w = 24
        filled = int(bar_w * frac)
        bar = (green if frac >= 1.0 else cyan)(full * filled) + dim(empty * (bar_w - filled))
        print(f"\r{label:<14} {bar} {frac * 100:5.1f}%  {extra}  {dim(eta)}   ".rstrip() + "   ",
              end="", file=self.err, flush=True)
        if frac >= 1.0:
            print(file=self.err)

    # ---- tabela tekstowa
    def table(self, headers: List[str], rows: List[List[str]]) -> None:
        for ln in format_table(headers, rows):
            self.line(ln)

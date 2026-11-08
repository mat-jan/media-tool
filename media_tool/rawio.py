"""Odczyt surowych danych (urządzenie blokowe albo zwykły plik) – wyłącznie do odczytu."""
from __future__ import annotations

import errno
import os
from typing import List, Optional

from .errors import RawAccessError
from .util import disable_cache


def add_range(ranges: List[list], offset: int, length: int) -> None:
    """Dodaje [offset, length] do listy zakresów, scalając przyległe."""
    if ranges and ranges[-1][0] + ranges[-1][1] == offset:
        ranges[-1][1] += length
    else:
        ranges.append([offset, length])


def overlaps(ranges: List[list], offset: int, length: int) -> bool:
    end = offset + length
    return any(o < end and offset < o + l for o, l in ranges)


class RawReader:
    """Czyta bajty z danej ścieżki. Nigdy nie otwiera do zapisu."""

    def __init__(self, path: str, no_cache: bool = True):
        self.path = path
        try:
            self.f = open(path, "rb", buffering=0)
        except PermissionError:
            raise RawAccessError(
                f"Brak uprawnień do odczytu {path}. Uruchom program z uprawnieniami administratora "
                f"(macOS/Linux: sudo, Windows: terminal jako administrator).")
        except FileNotFoundError:
            raise RawAccessError(f"Nie znaleziono {path}")
        except OSError as e:
            if e.errno in (errno.EBUSY, errno.EACCES):
                raise RawAccessError(f"Nie można otworzyć {path}: {e.strerror}")
            raise RawAccessError(f"Nie można otworzyć {path}: {e}")
        if no_cache:
            disable_cache(self.f.fileno())

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_at(self, offset: int, length: int) -> bytes:
        """Czyta do `length` bajtów; krótszy wynik oznacza koniec danych. Rzuca OSError przy błędzie I/O."""
        self.f.seek(offset)
        chunks = []
        got = 0
        while got < length:
            part = self.f.read(length - got)
            if not part:
                break
            chunks.append(part)
            got += len(part)
        return b"".join(chunks)

    def read_resilient(self, offset: int, length: int, sector: int, bad: List[list]) -> bytes:
        """Jak read_at, ale przy błędzie I/O czyta sektor po sektorze; nieczytelne sektory = zera
        i zapis w `bad`. Zwraca dokładnie `length` bajtów, chyba że napotkano koniec danych."""
        try:
            return self.read_at(offset, length)
        except OSError:
            pass
        sector = max(sector, 1)
        out = bytearray()
        pos = offset
        end = offset + length
        while pos < end:
            n = min(sector, end - pos)
            try:
                piece = self.read_at(pos, n)
            except OSError:
                piece = b"\x00" * n
                add_range(bad, pos, n)
            out += piece
            if len(piece) < n:
                break
            pos += n
        return bytes(out)


def file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None

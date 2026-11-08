"""Analiza tablicy partycji (MBR/GPT) z surowych danych – tylko do odczytu."""
from __future__ import annotations

import struct
import uuid
import zlib
from typing import Any, Callable, Dict, List

GPT_TYPES = {
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": "EFI System",
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": "Microsoft basic data",
    "E3C9E316-0B5C-4DB8-817D-F92DF00215AE": "Microsoft reserved",
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": "Linux filesystem",
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": "Linux swap",
    "48465300-0000-11AA-AA11-00306543ECAC": "Apple HFS+",
    "7C3457EF-0000-11AA-AA11-00306543ECAC": "Apple APFS",
    "426F6F74-0000-11AA-AA11-00306543ECAC": "Apple Boot",
}
MBR_TYPES = {0x00: "pusty", 0x01: "FAT12", 0x04: "FAT16", 0x06: "FAT16B", 0x07: "NTFS/exFAT",
             0x0B: "FAT32 (CHS)", 0x0C: "FAT32 (LBA)", 0x0E: "FAT16 (LBA)", 0x0F: "Rozszerzona (LBA)",
             0x82: "Linux swap", 0x83: "Linux", 0xAF: "Apple HFS/HFS+", 0xEE: "Ochronny MBR (GPT)",
             0xEF: "EFI System"}

Reader = Callable[[int, int], bytes]


def _guid(raw: bytes) -> str:
    return str(uuid.UUID(bytes_le=raw)).upper()


def _parse_gpt_header(raw: bytes) -> Dict[str, Any]:
    if len(raw) < 92 or raw[:8] != b"EFI PART":
        return {"valid": False}
    hsize = struct.unpack_from("<I", raw, 12)[0]
    crc = struct.unpack_from("<I", raw, 16)[0]
    crc_ok = False
    if 92 <= hsize <= len(raw):
        tmp = bytearray(raw[:hsize])
        tmp[16:20] = b"\x00\x00\x00\x00"
        crc_ok = zlib.crc32(bytes(tmp)) & 0xFFFFFFFF == crc
    cur, backup, first, last = struct.unpack_from("<QQQQ", raw, 24)
    ent_lba, n_ent, ent_size, ent_crc = struct.unpack_from("<QIII", raw, 72)
    return {"valid": True, "crc_ok": crc_ok, "current_lba": cur, "backup_lba": backup,
            "first_usable_lba": first, "last_usable_lba": last, "disk_guid": _guid(raw[56:72]),
            "entries_lba": ent_lba, "entries": n_ent, "entry_size": ent_size, "entries_crc": ent_crc}


def inspect_table(read: Reader, size: int) -> Dict[str, Any]:
    """Zwraca opis schematu partycjonowania; `read(offset, length)` czyta surowe dane."""
    res: Dict[str, Any] = {"scheme": "brak", "sector_size": None, "partitions": [], "problems": []}
    first = read(0, 4096)
    if len(first) < 512:
        res["problems"].append("Za mało danych, by odczytać pierwszy sektor")
        return res
    has_mbr_sig = first[510:512] == b"\x55\xaa"
    mbr_parts = []
    if has_mbr_sig:
        for i in range(4):
            e = first[446 + 16 * i: 462 + 16 * i]
            ptype = e[4]
            start, count = struct.unpack_from("<II", e, 8)
            if ptype:
                mbr_parts.append({"index": i + 1, "type_code": f"0x{ptype:02X}",
                                  "type": MBR_TYPES.get(ptype, "nieznany"), "bootable": e[0] == 0x80,
                                  "start_lba": start, "sectors": count})
    for sector in (512, 4096):
        raw = read(sector, 512)
        if raw[:8] == b"EFI PART":
            return _inspect_gpt(read, size, sector, res, mbr_parts)
    if has_mbr_sig:
        res.update(scheme="MBR", partitions=mbr_parts)
        res["sector_size"] = 512
    return res


def _inspect_gpt(read: Reader, size: int, sector: int, res: dict, mbr_parts: list) -> dict:
    res["scheme"] = "GPT"
    res["sector_size"] = sector
    res["protective_mbr"] = bool(mbr_parts) and mbr_parts[0]["type_code"] == "0xEE"
    if not res["protective_mbr"]:
        res["problems"].append("Brak ochronnego MBR (typ 0xEE)")
    h = _parse_gpt_header(read(sector, 512))
    res["header"] = h
    if not h["crc_ok"]:
        res["problems"].append("CRC nagłówka głównego GPT niepoprawny")
    n, esz = h["entries"], h["entry_size"]
    if not (0 < n <= 4096 and 128 <= esz <= 1024):
        res["problems"].append("Nietypowe parametry tablicy wpisów GPT")
        return res
    raw = read(h["entries_lba"] * sector, n * esz)
    if len(raw) < n * esz:
        res["problems"].append("Nie udało się odczytać pełnej tablicy wpisów GPT")
    elif zlib.crc32(raw) & 0xFFFFFFFF != h["entries_crc"]:
        res["problems"].append("CRC tablicy wpisów GPT niepoprawny")
    for i in range(min(n, len(raw) // esz)):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\x00" * 16:
            continue
        tg = _guid(e[:16])
        first, last = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16le", "replace").split("\x00")[0]
        res["partitions"].append({"index": i + 1, "type_guid": tg, "type": GPT_TYPES.get(tg, "nieznany"),
                                  "partuuid": _guid(e[16:32]), "name": name, "start_lba": first,
                                  "end_lba": last, "size_bytes": (last - first + 1) * sector})
    # nagłówek zapasowy
    last_lba = size // sector - 1 if size else None
    if h["backup_lba"]:
        b = _parse_gpt_header(read(h["backup_lba"] * sector, 512))
        res["backup_header_ok"] = b.get("valid", False) and b.get("crc_ok", False)
        if not res["backup_header_ok"]:
            res["problems"].append("Zapasowy nagłówek GPT nieczytelny lub niepoprawny")
        if last_lba is not None and h["backup_lba"] != last_lba:
            res["problems"].append("Zapasowy nagłówek GPT nie leży na końcu nośnika (obraz skopiowany na "
                                   "inny rozmiar lub nośnik został zmodyfikowany)")
    return res

"""Modele danych. Brakujące informacje to zawsze None -> w raportach `NIEDOSTĘPNE`."""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

UNAVAILABLE = "NIEDOSTĘPNE"


@dataclass
class Partition:
    identifier: Optional[str] = None      # np. disk4s1, sdb1, "Disk 1 / Partition 2"
    path: Optional[str] = None
    size_bytes: Optional[int] = None
    offset_bytes: Optional[int] = None
    part_type: Optional[str] = None       # typ partycji (GUID / kod MBR / Content)
    fs_type: Optional[str] = None         # system plików
    volume_label: Optional[str] = None    # etykieta woluminu
    fs_uuid: Optional[str] = None         # UUID systemu plików
    partuuid: Optional[str] = None        # PARTUUID / GUID partycji
    mount_points: Optional[List[str]] = None
    read_only: Optional[bool] = None
    encrypted: Optional[bool] = None


@dataclass
class Health:
    status: Optional[str] = None          # np. PASSED / FAILED / Verified / Healthy
    temperature_c: Optional[float] = None
    power_on_hours: Optional[int] = None
    error_count: Optional[int] = None
    source: Optional[str] = None          # skąd dane: smartctl / diskutil / Get-PhysicalDisk
    note: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Device:
    path: str                                   # ścieżka urządzenia (np. /dev/disk4)
    system_name: Optional[str] = None           # nazwa systemowa (disk4 / sdb / PhysicalDrive1)
    name: Optional[str] = None                  # nazwa nośnika podana przez system
    vendor: Optional[str] = None
    model: Optional[str] = None
    firmware: Optional[str] = None
    serial: Optional[str] = None                # numer seryjny urządzenia (jeśli system go podaje)
    controller_serial: Optional[str] = None
    card_serial: Optional[str] = None           # numer seryjny karty SD/MMC (CID)
    reader_serial: Optional[str] = None         # numer seryjny czytnika / mostka USB
    usb_vid: Optional[str] = None
    usb_pid: Optional[str] = None
    usb_revision: Optional[str] = None
    wwn: Optional[str] = None
    eui64: Optional[str] = None
    uuid: Optional[str] = None                  # UUID dysku / kontenera, jeśli istnieje
    partuuid: Optional[str] = None
    device_type: Optional[str] = None           # SSD / HDD / karta SD / nośnik USB ...
    bus_type: Optional[str] = None              # magistrala raportowana przez system
    interface: Optional[str] = None             # USB / SATA / NVMe / SD / MMC
    size_nominal_bytes: Optional[int] = None    # rozmiar raportowany przez warstwę sprzętową
    size_detected_bytes: Optional[int] = None   # rozmiar widziany przez system (blokowy)
    sector_size: Optional[int] = None           # logiczny rozmiar sektora
    physical_sector_size: Optional[int] = None
    block_size: Optional[int] = None            # rozmiar bloku urządzenia
    sector_count: Optional[int] = None
    partition_table: Optional[str] = None       # GPT / MBR / brak
    partitions: List[Partition] = field(default_factory=list)
    filesystem: Optional[str] = None
    volume_label: Optional[str] = None
    mount_points: Optional[List[str]] = None
    mounted: Optional[bool] = None
    read_only: Optional[bool] = None
    is_system: Optional[bool] = None
    removable: Optional[bool] = None
    internal: Optional[bool] = None
    encryption: Optional[str] = None
    health: Optional[Health] = None
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    collected_at: str = field(default_factory=lambda: datetime.datetime.now()
                              .astimezone().isoformat(timespec="seconds"))

    # ------------------------------------------------------------ pomocnicze
    def short_name(self) -> str:
        return " ".join(x for x in (self.vendor, self.model or self.name) if x) or self.path

    def identity(self) -> Dict[str, Any]:
        """Dane, które nie powinny się zmienić między wykryciem a operacją."""
        return {
            "path": self.path, "vendor": self.vendor, "model": self.model, "serial": self.serial,
            "reader_serial": self.reader_serial, "card_serial": self.card_serial, "wwn": self.wwn,
            "usb_vid": self.usb_vid, "usb_pid": self.usb_pid,
            "size": self.size_detected_bytes, "table": self.partition_table,
            "partitions": [[p.identifier, p.size_bytes, p.partuuid] for p in self.partitions],
        }

    def stable_identity(self) -> Dict[str, Any]:
        """Tożsamość niezależna od numeru, jaki system nadał przy ostatnim podłączeniu (do wznawiania)."""
        return {"vendor": self.vendor, "model": self.model, "serial": self.serial,
                "reader_serial": self.reader_serial, "card_serial": self.card_serial, "wwn": self.wwn,
                "usb_vid": self.usb_vid, "usb_pid": self.usb_pid, "size": self.size_detected_bytes}

    def state(self) -> Dict[str, Any]:
        """Identyfikacja + stan zamontowania (do operacji zmieniających stan)."""
        s = self.identity()
        s["mounts"] = sorted(self.mount_points or [])
        s["part_mounts"] = sorted(m for p in self.partitions for m in (p.mount_points or []))
        return s

    def identity_fingerprint(self) -> str:
        return _fp(self.identity())

    def state_fingerprint(self) -> str:
        return _fp(self.state())

    def to_dict(self, display: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        return _display(d) if display else d


def _fp(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _display(obj: Any) -> Any:
    """None -> `NIEDOSTĘPNE` (rekurencyjnie), z wyjątkiem pustych słowników `extra`/`details`."""
    if isinstance(obj, dict):
        return {k: (v if k in ("extra", "details") else _display(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_display(v) for v in obj]
    return UNAVAILABLE if obj is None else obj


def device_from_dict(d: Dict[str, Any]) -> Device:
    """Odtwarza Device z raportu (wartości `NIEDOSTĘPNE` wracają jako None)."""
    d = _undisplay(d)
    known = {f.name for f in fields(Device)}
    parts = [Partition(**{k: v for k, v in p.items() if k in {f.name for f in fields(Partition)}})
             for p in d.get("partitions") or []]
    h = d.get("health")
    health = Health(**{k: v for k, v in h.items() if k in {f.name for f in fields(Health)}}) if h else None
    kw = {k: v for k, v in d.items() if k in known and k not in ("partitions", "health")}
    return Device(partitions=parts, health=health, **kw)


def _undisplay(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _undisplay(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_undisplay(v) for v in obj]
    return None if obj == UNAVAILABLE else obj

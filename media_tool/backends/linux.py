"""Backend Linux: lsblk (JSON) + sysfs + udevadm (+ smartctl, jeśli jest)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import Device, Partition
from ..util import run, run_json, which
from .base import Backend, BackendError

SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/usr", "/var", "/home"}
SKIP_PREFIXES = ("loop", "ram", "zram", "dm-", "md", "nbd", "sr", "fd")


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _read(path: str) -> Optional[str]:
    try:
        return _clean(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _mounts(node: dict) -> List[str]:
    m = node.get("mountpoints")
    if m is None:
        m = [node.get("mountpoint")]
    return [x for x in m if x]


def _walk(node: dict):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def usb_info_from_sysfs(name: str, sys_root: str = "/sys") -> Dict[str, Optional[str]]:
    """Idzie w górę drzewa sysfs do urządzenia USB i czyta idVendor/idProduct/bcdDevice/serial."""
    try:
        p = Path(os.path.realpath(f"{sys_root}/class/block/{name}"))
    except OSError:
        return {}
    for parent in [p, *p.parents]:
        if (parent / "idVendor").exists():
            return {"vid": _read(str(parent / "idVendor")), "pid": _read(str(parent / "idProduct")),
                    "rev": _read(str(parent / "bcdDevice")), "serial": _read(str(parent / "serial")),
                    "manufacturer": _read(str(parent / "manufacturer"))}
        if str(parent) in ("/", sys_root):
            break
    return {}


def mmc_info_from_sysfs(name: str, sys_root: str = "/sys") -> Dict[str, Optional[str]]:
    base = f"{sys_root}/block/{name}/device"
    keys = ("name", "manfid", "oemid", "serial", "date", "fwrev", "hwrev", "cid", "type")
    info = {k: _read(f"{base}/{k}") for k in keys}
    return info if any(info.values()) else {}


def udev_props(path: str) -> Dict[str, str]:
    r = run(["udevadm", "info", "--query=property", f"--name={path}"], timeout=10)
    props: Dict[str, str] = {}
    if r.ok:
        for ln in r.out.splitlines():
            k, _, v = ln.partition("=")
            if k:
                props[k] = v
    return props


class LinuxBackend(Backend):
    name = "Linux"
    tools = ["lsblk", "udevadm", "umount", "udisksctl", "eject"]
    smart_uses_raw = False
    sys_root = "/sys"

    def list_devices(self, include_virtual: bool = False) -> List[Device]:
        data = run_json(["lsblk", "-J", "-b", "-O"], timeout=30)
        if data is None:
            raise BackendError("lsblk nie jest dostępny lub nie zwrócił danych (pakiet util-linux)")
        return self.parse_lsblk(data, include_virtual)

    def parse_lsblk(self, data: dict, include_virtual: bool = False) -> List[Device]:
        devices = []
        for node in data.get("blockdevices", []):
            name = node.get("name", "")
            if node.get("type") != "disk" and not (include_virtual and node.get("type") in ("loop", "rom")):
                continue
            if name.startswith(SKIP_PREFIXES) and not include_virtual:
                continue
            devices.append(self._device(node))
        return devices

    def _device(self, node: dict) -> Device:
        name = node["name"]
        path = node.get("path") or f"/dev/{name}"
        udev = udev_props(path) if self.sys_root == "/sys" else {}
        usb = usb_info_from_sysfs(name, self.sys_root) if (node.get("tran") == "usb" or udev.get("ID_BUS") == "usb") else {}
        is_mmc = name.startswith("mmcblk")
        mmc = mmc_info_from_sysfs(name, self.sys_root) if is_mmc else {}
        sd_in_reader = udev.get("ID_DRIVE_FLASH_SD") == "1" or udev.get("ID_DRIVE_MEDIA_FLASH_SD") == "1"

        children = list(_walk(node))[1:]
        all_nodes = [node] + children
        mounts = sorted({m for n in all_nodes for m in _mounts(n)})
        tran = _clean(node.get("tran")) or ("mmc" if is_mmc else None)
        rota = node.get("rota")
        size = _to_int(node.get("size"))

        d = Device(
            path=path, system_name=name, name=_clean(node.get("model")),
            vendor=_clean(node.get("vendor")) or _clean(usb.get("manufacturer")) or _clean(udev.get("ID_VENDOR")),
            model=_clean(node.get("model")) or _clean(mmc.get("name")), firmware=_clean(node.get("rev")) or _clean(mmc.get("fwrev")),
            bus_type=tran, size_detected_bytes=size,
            size_nominal_bytes=_sectors_to_bytes(_read(f"{self.sys_root}/block/{name}/size")),
            sector_size=_to_int(node.get("log-sec")), physical_sector_size=_to_int(node.get("phy-sec")),
            block_size=_to_int(node.get("phy-sec")),
            partition_table=_pt(node.get("pttype")),
            read_only=bool(node.get("ro")) if node.get("ro") is not None else None,
            removable=bool(node.get("rm")) if node.get("rm") is not None else None,
            wwn=_clean(node.get("wwn")), mount_points=mounts, mounted=bool(mounts),
            uuid=_clean(node.get("ptuuid")),
        )
        if d.sector_size and size:
            d.sector_count = size // d.sector_size
        if d.wwn and d.wwn.lower().startswith("eui."):
            d.eui64 = d.wwn[4:]
        elif name.startswith("nvme"):
            d.eui64 = _read(f"{self.sys_root}/block/{name}/eui")
        d.internal = None if d.removable is None else (not d.removable and tran != "usb")
        d.usb_vid, d.usb_pid, d.usb_revision = (usb.get("vid"), usb.get("pid"), usb.get("rev"))

        serial = _clean(node.get("serial")) or _clean(udev.get("ID_SERIAL_SHORT"))
        if is_mmc:
            d.card_serial = _clean(mmc.get("serial"))
            d.device_type = "karta SD/MMC"
            d.interface = "MMC" if mmc.get("type") == "MMC" else "SD"
            d.extra["mmc"] = mmc
            d.notes.append("Numer seryjny karty odczytany z rejestru CID karty (sysfs).")
        else:
            d.serial = serial
            if usb or sd_in_reader or tran == "usb":
                d.notes.append("Numer seryjny pochodzi z interfejsu USB – dla kart SD w czytniku może "
                               "dotyczyć czytnika, a nie samej karty.")
        if sd_in_reader:
            d.reader_serial, d.serial = serial, None
            d.device_type = "karta SD w czytniku USB"
            d.interface = "USB (czytnik) / SD"
            d.notes.append("Wykryto kartę SD w czytniku: numer seryjny należy do czytnika; "
                           "numer karty nie jest dostępny przez USB.")
        elif not is_mmc:
            d.device_type = "NVMe SSD" if name.startswith("nvme") else (
                "HDD" if rota else "SSD" if rota is not None else None)
            if usb or tran == "usb":
                d.device_type = f"{d.device_type or 'nośnik'} / USB" if d.device_type else "nośnik USB"
            d.interface = {"usb": "USB", "sata": "SATA", "nvme": "NVMe", "ata": "SATA"}.get(tran or "", tran)

        # partycje
        for c in children:
            if c.get("type") not in ("part", "disk", "lvm", "crypt"):
                continue
            if c.get("type") == "part":
                d.partitions.append(Partition(
                    identifier=c.get("name"), path=c.get("path"), size_bytes=_to_int(c.get("size")),
                    offset_bytes=(_to_int(c.get("start")) or 0) * (d.sector_size or 512) if c.get("start") is not None else None,
                    part_type=_clean(c.get("parttype")), fs_type=_clean(c.get("fstype")),
                    volume_label=_clean(c.get("label")), fs_uuid=_clean(c.get("uuid")),
                    partuuid=_clean(c.get("partuuid")), mount_points=_mounts(c),
                    read_only=bool(c.get("ro")) if c.get("ro") is not None else None,
                    encrypted=True if c.get("fstype") == "crypto_LUKS" else None))
        fs = sorted({p.fs_type for p in d.partitions if p.fs_type} | ({node["fstype"]} if node.get("fstype") else set()))
        d.filesystem = ", ".join(fs) or None
        d.volume_label = ", ".join(p.volume_label for p in d.partitions if p.volume_label) or _clean(node.get("label"))
        if any(p.fs_type == "crypto_LUKS" for p in d.partitions) or node.get("fstype") == "crypto_LUKS":
            d.encryption = "LUKS"
        elif d.partitions:
            d.encryption = "nie wykryto"
        d.is_system = any(m in SYSTEM_MOUNTS for n in all_nodes for m in _mounts(n))
        if d.is_system:
            d.notes.append("Dysk systemowy.")
        return d

    def unmount(self, device: Device) -> List[str]:
        msgs = []
        targets = [m for p in device.partitions for m in (p.mount_points or [])]
        targets += [m for m in (device.mount_points or []) if m not in targets]
        if not targets:
            return ["Nic do odmontowania."]
        for m in sorted(set(targets), key=len, reverse=True):
            part = next((p for p in device.partitions if m in (p.mount_points or [])), None)
            if which("udisksctl") and part and part.path:
                r = run(["udisksctl", "unmount", "-b", part.path], timeout=60)
            else:
                r = run(["umount", m], timeout=60)
            if not r.ok:
                raise BackendError(f"Nie udało się odmontować {m}: {(r.err or r.out).strip()}")
            msgs.append(f"Odmontowano {m}")
        return msgs

    def power_off(self, device: Device) -> str:
        if which("udisksctl"):
            r = run(["udisksctl", "power-off", "-b", device.path], timeout=60)
        elif which("eject"):
            r = run(["eject", device.path], timeout=60)
        else:
            raise BackendError("Brak udisksctl/eject – nie można bezpiecznie odłączyć")
        if not r.ok:
            raise BackendError((r.err or r.out).strip() or "odłączenie nie powiodło się")
        return (r.out or "OK").strip()


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sectors_to_bytes(v: Optional[str]) -> Optional[int]:
    """sysfs `size` jest zawsze w sektorach po 512 B."""
    n = _to_int(v)
    return n * 512 if n is not None else None


def _pt(v: Optional[str]) -> Optional[str]:
    if not v:
        return "brak"
    return {"gpt": "GPT", "dos": "MBR", "mbr": "MBR"}.get(v.lower(), v)

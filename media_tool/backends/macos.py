"""Backend macOS: diskutil + system_profiler (+ smartctl, jeśli jest)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..models import Device, Health, Partition
from ..util import run, run_json, run_plist
from .base import Backend, BackendError

HEX4 = re.compile(r"0x([0-9a-fA-F]{4})")


def _hex4(value: Any) -> Optional[str]:
    m = HEX4.search(str(value or ""))
    return m.group(1).upper() if m else None


def _uniq_join(items) -> str:
    seen = []
    for it in items:
        for part in str(it or "").split(", "):
            if part and part not in seen:
                seen.append(part)
    return ", ".join(seen)


def _whole(ident: str) -> str:
    return re.sub(r"s\d+$", "", ident)


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ------------------------------------------------------------------ system_profiler
def index_profiler(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """bsd_name -> {'self': {...}, 'usb': {...}, 'reader': {...}}.

    Kontekst USB to najbliższy przodek będący urządzeniem USB (nie sumujemy pól hubów,
    żeby nie przypisać numeru seryjnego huba do nośnika)."""
    found: Dict[str, Dict[str, Any]] = {}

    def scalars(node: dict) -> dict:
        return {k: v for k, v in node.items() if not isinstance(v, (dict, list))}

    def walk(node: Any, ctx: dict) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n, ctx)
            return
        if not isinstance(node, dict):
            return
        sc = scalars(node)
        c = dict(ctx)
        if "product_id" in node or "vendor_id" in node:
            c["usb"] = sc
        if any(k.startswith("spcardreader_") for k in node):
            c["reader"] = sc
        if node.get("bsd_name"):
            entry = found.setdefault(node["bsd_name"], {})
            entry["self"] = sc
            for key in ("usb", "reader"):
                if key in c:
                    entry[key] = c[key]
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v, c)

    walk(list(data.values()), {})
    return found


def system_disks(entries: List[dict]) -> set:
    """Całe dyski, na których leży system (wolumin zamontowany w / lub /System/Volumes/Data)."""
    sys_mounts = {"/", "/System/Volumes/Data"}
    result = set()
    for e in entries:
        vols = e.get("APFSVolumes") or []
        if any(v.get("MountPoint") in sys_mounts for v in vols):
            for st in e.get("APFSPhysicalStores") or []:
                result.add(_whole(st["DeviceIdentifier"]))
        for p in e.get("Partitions") or []:
            if p.get("MountPoint") in sys_mounts:
                result.add(_whole(p["DeviceIdentifier"]))
        if e.get("MountPoint") in sys_mounts:
            result.add(_whole(e["DeviceIdentifier"]))
    return result


class MacOSBackend(Backend):
    name = "macOS"
    tools = ["diskutil", "system_profiler"]
    smart_uses_raw = False

    def list_devices(self, include_virtual: bool = False) -> List[Device]:
        listing = run_plist(["diskutil", "list", "-plist"])
        if not listing:
            raise BackendError("diskutil nie zwrócił listy dysków")
        entries = listing.get("AllDisksAndPartitions", [])
        profiler = index_profiler(run_json(
            ["system_profiler", "-json", "SPUSBDataType", "SPCardReaderDataType", "SPNVMeDataType",
             "SPSerialATADataType", "SPThunderboltDataType"], timeout=60) or {})
        sysdisks = system_disks(entries)
        store_volumes: Dict[str, List[dict]] = {}
        for e in entries:
            for st in e.get("APFSPhysicalStores") or []:
                store_volumes.setdefault(st["DeviceIdentifier"], []).extend(e.get("APFSVolumes") or [])

        devices: List[Device] = []
        for e in entries:
            ident = e["DeviceIdentifier"]
            info = run_plist(["diskutil", "info", "-plist", ident]) or {}
            virtual = info.get("VirtualOrPhysical") == "Virtual" or info.get("BusProtocol") == "Disk Image"
            if virtual and not include_virtual:
                continue
            devices.append(self._device(ident, e, info, profiler.get(ident, {}),
                                        ident in sysdisks, store_volumes, virtual))
        return devices

    # ------------------------------------------------------------------
    def _device(self, ident, entry, info, prof, is_system, store_volumes, virtual) -> Device:
        usb, reader, own = prof.get("usb", {}), prof.get("reader", {}), prof.get("self", {})
        bus = _clean(info.get("BusProtocol"))
        content = info.get("Content")
        table = {"GUID_partition_scheme": "GPT", "FDisk_partition_scheme": "MBR",
                 "Apple_partition_scheme": "APM"}.get(content)
        size = info.get("TotalSize") or info.get("Size") or entry.get("Size")
        removable = info.get("RemovableMediaOrExternalDevice")
        if removable is None:
            removable = info.get("Removable")
        d = Device(
            path=f"/dev/{ident}", system_name=ident, name=_clean(info.get("MediaName")),
            model=_clean(own.get("device_model")) or _clean(info.get("MediaName")),
            vendor=_clean(usb.get("manufacturer")),
            firmware=_clean(own.get("device_revision")),
            bus_type=bus, size_detected_bytes=size,
            size_nominal_bytes=own.get("size_in_bytes"),
            sector_size=info.get("DeviceBlockSize"), block_size=info.get("DeviceBlockSize"),
            sector_count=(size // info["DeviceBlockSize"]) if size and info.get("DeviceBlockSize") else None,
            partition_table=table or ("brak" if content else None),
            read_only=(not info["WritableMedia"]) if "WritableMedia" in info else None,
            is_system=is_system, removable=removable, internal=info.get("Internal"),
            uuid=_clean(info.get("DiskUUID")),
        )
        # --- identyfikatory sprzętowe
        d.usb_vid = _hex4(usb.get("vendor_id"))
        d.usb_pid = _hex4(usb.get("product_id"))
        d.usb_revision = _clean(usb.get("bcd_device"))
        if usb:
            # Numer z interfejsu USB należy do urządzenia widocznego dla systemu (mostek/czytnik).
            d.serial = _clean(usb.get("serial_num"))
            d.notes.append("Numer seryjny pochodzi z interfejsu USB – dla kart SD w czytniku może "
                           "dotyczyć czytnika, a nie samej karty.")
        else:
            d.serial = _clean(own.get("device_serial"))
        if reader:
            d.extra["card_reader"] = reader
            card = {k: v for k, v in own.items() if k != "bsd_name"}
            d.extra["card"] = card
            d.bus_type = bus or "Card Reader"
            serial_key = next((k for k in card if "serial" in k.lower()), None)
            d.card_serial = _clean(card[serial_key]) if serial_key else None
            d.notes.append("Numer seryjny karty SD zależy od czytnika/sterownika; jeśli system go nie "
                           "udostępnia, pole pozostaje NIEDOSTĘPNE.")
        # --- interfejs i typ
        d.interface = self._interface(bus, own, bool(reader), bool(usb))
        d.device_type = self._type(info, bus, bool(reader), virtual)
        # --- partycje / woluminy
        d.partitions = [self._partition(p, store_volumes) for p in entry.get("Partitions") or []]
        mounts: List[str] = []
        for p in d.partitions:
            mounts += p.mount_points or []
        if entry.get("MountPoint"):
            mounts.append(entry["MountPoint"])
        d.mount_points = sorted(set(mounts))
        d.mounted = bool(d.mount_points)
        fs = sorted({p.fs_type for p in d.partitions if p.fs_type})
        d.filesystem = ", ".join(fs) or _clean(info.get("FilesystemName"))
        d.volume_label = _uniq_join(p.volume_label for p in d.partitions) or _clean(info.get("VolumeName"))
        enc = [p.encrypted for p in d.partitions if p.encrypted is not None]
        d.encryption = None if not enc else ("tak (FileVault/APFS)" if any(enc) else "nie wykryto")
        # --- zdrowie z diskutil (SMARTStatus) – pełny odczyt dopiero w read_health
        if is_system:
            d.notes.append("Dysk systemowy.")
        return d

    @staticmethod
    def _interface(bus, own, is_reader, is_usb) -> Optional[str]:
        if "spnvme_trim_support" in own:
            return "NVMe"
        if is_reader and not is_usb:
            return "SD"
        mapping = {"USB": "USB", "SATA": "SATA", "Secure Digital": "SD", "PCI-Express": "PCIe",
                   "Thunderbolt": "Thunderbolt", "Apple Fabric": "Apple Fabric (NVMe)"}
        return mapping.get(bus, bus)

    @staticmethod
    def _type(info, bus, is_reader, virtual) -> Optional[str]:
        if bus == "Disk Image":
            return "obraz dysku (wirtualny)"
        if virtual:
            return "wirtualny kontener (np. APFS)"
        if bus == "Secure Digital" or (is_reader and bus != "USB"):
            return "karta SD"
        if "SolidState" in info:
            kind = "SSD" if info["SolidState"] else "HDD"
            if bus == "USB" and (info.get("RemovableMediaOrExternalDevice") or info.get("Removable")):
                return f"{kind} / nośnik USB"
            return kind
        if bus == "USB":
            return "nośnik USB"
        return None

    def _partition(self, p: dict, store_volumes) -> Partition:
        ident = p["DeviceIdentifier"]
        info = run_plist(["diskutil", "info", "-plist", ident]) or {}
        vols = store_volumes.get(ident, [])
        mounts = [p["MountPoint"]] if p.get("MountPoint") else []
        mounts += [v["MountPoint"] for v in vols if v.get("MountPoint")]
        encrypted = None
        for v in vols:
            vi = run_plist(["diskutil", "info", "-plist", v["DeviceIdentifier"]]) or {}
            if "FileVault" in vi:
                encrypted = bool(encrypted) or bool(vi["FileVault"])
        if encrypted is None and "Encryption" in info:
            encrypted = bool(info["Encryption"])
        fs = _clean(info.get("FilesystemName") or info.get("FilesystemType"))
        if not fs and vols:
            fs = "APFS (kontener)"
        label = _clean(info.get("VolumeName")) or (_uniq_join(v.get("VolumeName") for v in vols) or None)
        return Partition(
            identifier=ident, path=f"/dev/{ident}", size_bytes=p.get("Size") or info.get("TotalSize"),
            offset_bytes=info.get("PartitionMapPartitionOffset"), part_type=_clean(p.get("Content") or info.get("Content")),
            fs_type=fs, volume_label=label, fs_uuid=_clean(info.get("VolumeUUID")),
            partuuid=_clean(info.get("DiskUUID")), mount_points=sorted(set(mounts)),
            # kontener APFS nie jest woluminem – flaga zapisu nie ma tu sensu
            read_only=None if vols else ((not info["WritableVolume"]) if "WritableVolume" in info else None),
            encrypted=encrypted,
        )

    # ------------------------------------------------------------------
    def raw_path(self, device: Device) -> str:
        return device.path.replace("/dev/disk", "/dev/rdisk", 1)

    def system_health(self, device: Device) -> Optional[Health]:
        info = run_plist(["diskutil", "info", "-plist", device.system_name or device.path]) or {}
        status = info.get("SMARTStatus")
        if not status:
            return None
        return Health(status=str(status), source="diskutil",
                      note="Tylko ogólny status SMART z systemu (zainstaluj smartmontools po więcej)."
                      if status != "Not Supported" else "Urządzenie nie obsługuje SMART przez system.")

    def unmount(self, device: Device) -> List[str]:
        r = run(["diskutil", "unmountDisk", device.path], timeout=120)
        if not r.ok:
            raise BackendError((r.err or r.out).strip() or "diskutil unmountDisk nie powiódł się")
        return [r.out.strip()]

    def power_off(self, device: Device) -> str:
        r = run(["diskutil", "eject", device.path], timeout=120)
        if not r.ok:
            raise BackendError((r.err or r.out).strip() or "diskutil eject nie powiódł się")
        return r.out.strip()

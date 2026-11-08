"""Backend Windows: PowerShell (Get-Disk / Get-PhysicalDisk / Get-Partition / Get-Volume / CIM)."""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from ..models import Device, Health, Partition
from ..util import run, which
from .base import Backend, BackendError, smartctl_health

PS_LIST = r"""
$ErrorActionPreference = 'SilentlyContinue'
$wmi = @(Get-CimInstance Win32_DiskDrive)
$ld  = @(Get-CimInstance Win32_LogicalDisk)
$out = foreach ($d in Get-Disk) {
  $pd = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq "$($d.Number)" } | Select-Object -First 1
  $w  = $wmi | Where-Object { $_.Index -eq $d.Number } | Select-Object -First 1
  $parent = $null
  if ($w -and $w.PNPDeviceID) {
    $parent = (Get-PnpDeviceProperty -InstanceId $w.PNPDeviceID -KeyName 'DEVPKEY_Device_Parent').Data
  }
  $parts = foreach ($p in Get-Partition -DiskNumber $d.Number) {
    $v = $p | Get-Volume
    $letters = @($p.AccessPaths | Where-Object { $_ -match '^[A-Z]:\\' })
    $vsn = $null
    if ($p.DriveLetter) { $vsn = ($ld | Where-Object { $_.DeviceID -eq "$($p.DriveLetter):" }).VolumeSerialNumber }
    [pscustomobject]@{
      Number = $p.PartitionNumber; Size = $p.Size; Offset = $p.Offset; Guid = $p.Guid
      GptType = $p.GptType; MbrType = $p.MbrType; Type = $p.Type; IsReadOnly = $p.IsReadOnly
      AccessPaths = $letters; DriveLetter = "$($p.DriveLetter)"
      FileSystem = $v.FileSystem; Label = $v.FileSystemLabel; VolumeId = $v.UniqueId
      VolumeSerial = $vsn
    }
  }
  [pscustomobject]@{
    Number = $d.Number; FriendlyName = $d.FriendlyName; Manufacturer = $w.Manufacturer; Model = $w.Model
    Serial = $d.SerialNumber; Firmware = $d.FirmwareVersion; Size = $d.Size
    LogicalSectorSize = $d.LogicalSectorSize; PhysicalSectorSize = $d.PhysicalSectorSize
    PartitionStyle = "$($d.PartitionStyle)"; IsSystem = $d.IsSystem; IsBoot = $d.IsBoot
    IsReadOnly = $d.IsReadOnly; IsOffline = $d.IsOffline; BusType = "$($d.BusType)"
    HealthStatus = "$($d.HealthStatus)"; UniqueId = $d.UniqueId; Guid = $d.Guid
    MediaType = "$($pd.MediaType)"; Pnp = $w.PNPDeviceID; Parent = $parent
    InterfaceType = $w.InterfaceType; Partitions = @($parts)
  }
}
ConvertTo-Json -InputObject @($out) -Depth 6 -Compress
"""

PS_RELIABILITY = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pd = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq "%d" } | Select-Object -First 1
$r = $pd | Get-StorageReliabilityCounter
[pscustomobject]@{ Health = "$($pd.HealthStatus)"; Operational = "$($pd.OperationalStatus)";
  Temp = $r.Temperature; Hours = $r.PowerOnHours; ReadErr = $r.ReadErrorsTotal;
  WriteErr = $r.WriteErrorsTotal } | ConvertTo-Json -Compress
"""

PS_EJECT = r"""
$sh = New-Object -ComObject Shell.Application
$ok = $false
foreach ($l in @(%s)) { $sh.Namespace(17).ParseName($l + '\').InvokeVerb('Eject'); $ok = $true }
if (-not $ok) { exit 3 }
"""


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class WindowsBackend(Backend):
    name = "Windows"
    tools = ["powershell", "pwsh"]
    smart_uses_raw = False

    def _ps(self) -> str:
        exe = which("powershell") or which("pwsh")
        if not exe:
            raise BackendError("PowerShell nie jest dostępny")
        return exe

    def _run_ps(self, script: str, timeout: float = 90):
        return run([self._ps(), "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)

    def list_devices(self, include_virtual: bool = False) -> List[Device]:
        r = self._run_ps(PS_LIST)
        if not r.ok or not r.out.strip():
            raise BackendError(f"PowerShell nie zwrócił listy dysków: {r.err.strip()}")
        try:
            data = json.loads(r.out)
        except ValueError as e:
            raise BackendError(f"Niepoprawna odpowiedź PowerShell: {e}")
        return self.parse_disks(data, include_virtual)

    def parse_disks(self, data: Any, include_virtual: bool = False) -> List[Device]:
        devices = []
        for disk in _as_list(data):
            bus = _clean(disk.get("BusType"))
            if bus in ("File Backed Virtual", "Virtual") and not include_virtual:
                continue
            devices.append(self._device(disk))
        return devices

    def _device(self, disk: dict) -> Device:
        num = disk.get("Number")
        bus = _clean(disk.get("BusType"))
        size = disk.get("Size")
        sec = disk.get("LogicalSectorSize")
        pnp = _clean(disk.get("Pnp")) or ""
        parent = _clean(disk.get("Parent")) or ""
        m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", parent)
        d = Device(
            path=f"\\\\.\\PHYSICALDRIVE{num}", system_name=f"PhysicalDrive{num}", name=_clean(disk.get("FriendlyName")),
            vendor=_clean(disk.get("Manufacturer")), model=_clean(disk.get("Model")) or _clean(disk.get("FriendlyName")),
            firmware=_clean(disk.get("Firmware")), serial=_clean(disk.get("Serial")),
            bus_type=bus, interface=_clean(disk.get("InterfaceType")) or bus,
            size_detected_bytes=size, size_nominal_bytes=size, sector_size=sec,
            physical_sector_size=disk.get("PhysicalSectorSize"), block_size=disk.get("PhysicalSectorSize"),
            sector_count=(size // sec) if size and sec else None,
            partition_table={"GPT": "GPT", "MBR": "MBR", "RAW": "brak"}.get(_clean(disk.get("PartitionStyle")) or "", _clean(disk.get("PartitionStyle"))),
            read_only=disk.get("IsReadOnly"), is_system=bool(disk.get("IsSystem") or disk.get("IsBoot")),
            removable=bus in ("USB", "SD", "MMC"), internal=bus not in ("USB", "SD", "MMC", "1394"),
            uuid=_clean(disk.get("Guid")),
        )
        uid = _clean(disk.get("UniqueId"))
        if uid:
            if uid.lower().startswith("eui."):
                d.eui64 = uid[4:]
            elif uid.lower().startswith(("naa.", "wwn")):
                d.wwn = uid
            else:
                d.extra["unique_id"] = uid
        if m:
            d.usb_vid, d.usb_pid = m.group(1).upper(), m.group(2).upper()
        if bus in ("SD", "MMC"):
            d.device_type = "karta SD/MMC"
        elif bus == "USB":
            d.device_type = "nośnik USB"
            d.notes.append("Numer seryjny pochodzi z interfejsu USB – dla kart SD w czytniku może "
                           "dotyczyć czytnika, a nie samej karty.")
        else:
            mt = _clean(disk.get("MediaType"))
            d.device_type = mt if mt and mt != "Unspecified" else None
        mounts: List[str] = []
        for p in _as_list(disk.get("Partitions")):
            paths = [x for x in _as_list(p.get("AccessPaths"))]
            mounts += paths
            vsn = _clean(p.get("VolumeSerial"))
            d.partitions.append(Partition(
                identifier=f"Disk {num} / Partition {p.get('Number')}", path=None,
                size_bytes=p.get("Size"), offset_bytes=p.get("Offset"),
                part_type=_clean(p.get("GptType")) or _clean(p.get("Type")) or _clean(p.get("MbrType")),
                fs_type=_clean(p.get("FileSystem")), volume_label=_clean(p.get("Label")),
                fs_uuid=vsn or _clean(p.get("VolumeId")), partuuid=_clean(p.get("Guid")),
                mount_points=paths, read_only=p.get("IsReadOnly")))
        d.mount_points = sorted(set(mounts))
        d.mounted = bool(d.mount_points)
        d.filesystem = ", ".join(sorted({p.fs_type for p in d.partitions if p.fs_type})) or None
        d.volume_label = ", ".join(p.volume_label for p in d.partitions if p.volume_label) or None
        d.encryption = None  # BitLocker wymaga osobnego, uprzywilejowanego zapytania – nie zgadujemy
        if d.is_system:
            d.notes.append("Dysk systemowy / rozruchowy.")
        return d

    def raw_path(self, device: Device) -> str:
        return device.path

    def read_health(self, device: Device) -> Health:
        h = smartctl_health(device.path)
        if h.status is not None:
            return h
        num = re.search(r"(\d+)$", device.path)
        if not num:
            return h
        r = self._run_ps(PS_RELIABILITY % int(num.group(1)), timeout=60)
        try:
            data = json.loads(r.out) if r.ok and r.out.strip() else None
        except ValueError:
            data = None
        if not data:
            return h
        errs = [x for x in (data.get("ReadErr"), data.get("WriteErr")) if isinstance(x, (int, float))]
        return Health(status=_clean(data.get("Health")), temperature_c=data.get("Temp"),
                      power_on_hours=data.get("Hours"), error_count=int(sum(errs)) if errs else None,
                      source="Get-PhysicalDisk", details={"operational": data.get("Operational")},
                      note=h.note)

    def _letters(self, device: Device) -> List[str]:
        return sorted({m.rstrip("\\") for m in (device.mount_points or []) if re.match(r"^[A-Z]:", m)})

    def unmount(self, device: Device) -> List[str]:
        return self._eject(device)

    def power_off(self, device: Device) -> str:
        return "; ".join(self._eject(device))

    def _eject(self, device: Device) -> List[str]:
        letters = self._letters(device)
        if not letters:
            raise BackendError("Brak liter dysków do wysunięcia (użyj 'Bezpieczne usuwanie sprzętu')")
        script = PS_EJECT % ",".join(f"'{l}'" for l in letters)
        r = self._run_ps(script, timeout=120)
        if not r.ok:
            raise BackendError(r.err.strip() or "wysunięcie nie powiodło się")
        return [f"Wysunięto {l}" for l in letters]

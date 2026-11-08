#!/usr/bin/env python3
"""Device discovery: collects info about storage devices (macOS + Linux)."""
import json
import platform
import plistlib
import shutil
import subprocess
from pathlib import Path

FIELDS = [
    "device", "name", "model", "vendor", "serial", "serial_dec", "firmware",
    "size_bytes", "size", "bus", "kind", "removable", "internal",
    "filesystem", "partitions", "mount_points", "smart",
    "manufacturer_id", "mfg_date", "vendor_id", "product_id",
]


# --------------------------------------------------------------- helpers
def run(cmd, binary=False):
    """Run a command, return stdout or None on any failure."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def run_json(cmd):
    out = run(cmd)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def run_plist(cmd):
    out = run(cmd, binary=True)
    if not out:
        return None
    try:
        return plistlib.loads(out)
    except Exception:
        return None


def human_size(n):
    if not isinstance(n, (int, float)) or n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1000


def to_decimal(value):
    """'0x1A2B' -> '6699'; non-numeric values give ''."""
    try:
        return str(int(str(value), 0))
    except (ValueError, TypeError):
        return ""


def new_device(**kw):
    d = {f: "" for f in FIELDS}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


def smart_status(path):
    """Overall SMART health via smartctl, '' if unavailable."""
    if not shutil.which("smartctl"):
        return ""
    data = run_json(["smartctl", "-H", "-j", path])
    try:
        return "PASSED" if data["smart_status"]["passed"] else "FAILED"
    except (TypeError, KeyError):
        return ""


# ----------------------------------------------------------------- macOS
def walk_profiler(node, ctx, found):
    """Index system_profiler JSON by bsd_name, inheriting ancestor fields."""
    if isinstance(node, list):
        for item in node:
            walk_profiler(item, ctx, found)
    elif isinstance(node, dict):
        scalars = {k: v for k, v in node.items() if not isinstance(v, (dict, list))}
        merged = {**ctx, **scalars}
        bsd = node.get("bsd_name")
        if bsd:
            found.setdefault(bsd, {}).update(merged)
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk_profiler(v, merged, found)


def mac_profiler():
    types = ["SPCardReaderDataType", "SPUSBDataType", "SPNVMeDataType",
             "SPSerialATADataType", "SPThunderboltDataType"]
    data = run_json(["system_profiler", "-json", *types]) or {}
    found = {}
    walk_profiler(list(data.values()), {}, found)
    return found


def first(d, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return str(d[k])
    return ""


def collect_macos():
    listing = run_plist(["diskutil", "list", "-plist"]) or {}
    profiler = mac_profiler()
    devices = []
    for disk in listing.get("AllDisksAndPartitions", []):
        ident = disk.get("DeviceIdentifier")
        info = run_plist(["diskutil", "info", "-plist", ident]) or {}
        if not info or info.get("VirtualOrPhysical") == "Virtual":
            continue  # skip synthesized APFS containers / disk images
        if info.get("BusProtocol") == "Disk Image":
            continue
        prof = profiler.get(ident, {})
        parts = disk.get("Partitions", [])
        mounts = [p["MountPoint"] for p in parts if p.get("MountPoint")]
        if disk.get("MountPoint"):
            mounts.append(disk["MountPoint"])
        fs = sorted({p.get("Content", "") for p in parts if p.get("Content")})
        # volumes mounted from APFS containers etc. are not in "Partitions";
        # fall back to the filesystem diskutil reports for the whole disk
        filesystem = info.get("FilesystemName") or ", ".join(fs)
        size = info.get("TotalSize") or info.get("Size") or 0
        serial = first(prof, "device_serial", "serial_num", "spcardreader_card-serialnumber")
        devices.append(new_device(
            device=f"/dev/{ident}",
            name=info.get("MediaName", ""),
            model=first(prof, "device_model", "_name") or info.get("MediaName", ""),
            vendor=first(prof, "manufacturer", "vendor_id"),
            serial=serial,
            serial_dec=to_decimal(serial) if serial.lower().startswith("0x") else "",
            firmware=first(prof, "device_revision", "bcd_device"),
            size_bytes=size,
            size=human_size(size),
            bus=info.get("BusProtocol", ""),
            kind=("SSD" if info.get("SolidState") else "HDD")
                 if "SolidState" in info else "",
            removable="yes" if info.get("RemovableMediaOrExternalDevice")
                              or info.get("Removable") else "no",
            internal="yes" if info.get("Internal") else "no",
            filesystem=filesystem,
            partitions=len(parts),
            mount_points=";".join(mounts),
            smart=first(prof, "smart_status") or info.get("SMARTStatus", "")
                  or smart_status(f"/dev/{ident}"),
            manufacturer_id=first(prof, "spcardreader_card-manufacturer-id"),
            mfg_date=first(prof, "spcardreader_card-manufacturing-date"),
            vendor_id=first(prof, "vendor_id", "spcardreader_vendor-id"),
            product_id=first(prof, "product_id", "spcardreader_device-id"),
        ))
    return devices


# ----------------------------------------------------------------- Linux
def read_sysfs(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def flatten_children(node):
    yield node
    for c in node.get("children") or []:
        yield from flatten_children(c)


def collect_linux():
    cols = "NAME,PATH,TYPE,SIZE,MODEL,VENDOR,SERIAL,REV,TRAN,RM,ROTA,FSTYPE,MOUNTPOINTS,LABEL"
    data = run_json(["lsblk", "-J", "-b", "-o", cols]) or {}
    devices = []
    for disk in data.get("blockdevices", []):
        if disk.get("type") != "disk" or disk["name"].startswith(("loop", "ram", "zram")):
            continue
        nodes = list(flatten_children(disk))[1:]
        mounts = [m for n in [disk, *nodes] for m in (n.get("mountpoints") or []) if m]
        fstypes = sorted({n["fstype"] for n in [disk, *nodes] if n.get("fstype")})
        name = disk["name"]
        sysdev = f"/sys/block/{name}/device"
        is_mmc = name.startswith("mmcblk")
        tran = disk.get("tran") or ("mmc" if is_mmc else "")
        rota = disk.get("rota")
        kind = "NVMe" if name.startswith("nvme") else (
            "SD/MMC" if is_mmc else ("HDD" if rota else "SSD" if rota is not None else ""))
        path = disk.get("path") or f"/dev/{name}"
        serial = (disk.get("serial") or "").strip() or read_sysfs(f"{sysdev}/serial")
        size = int(disk.get("size") or 0)
        devices.append(new_device(
            device=path,
            name=read_sysfs(f"{sysdev}/name"),
            model=(disk.get("model") or "").strip(),
            vendor=(disk.get("vendor") or "").strip(),
            serial=serial,
            serial_dec=to_decimal(serial) if serial.lower().startswith("0x") else "",
            firmware=(disk.get("rev") or "").strip() or read_sysfs(f"{sysdev}/fwrev"),
            size_bytes=size,
            size=human_size(size),
            bus=tran,
            kind=kind,
            removable="yes" if disk.get("rm") else "no",
            internal="no" if disk.get("rm") or tran == "usb" else "yes",
            filesystem=", ".join(fstypes),
            partitions=len(nodes),
            mount_points=";".join(mounts),
            smart=smart_status(path),
            manufacturer_id=read_sysfs(f"{sysdev}/manfid"),
            mfg_date=read_sysfs(f"{sysdev}/date"),
        ))
    return devices


def collect():
    """Return a list of device dicts for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return collect_macos()
    if system == "Linux":
        return collect_linux()
    raise RuntimeError(f"Unsupported platform: {system}")

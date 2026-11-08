"""Wspólne narzędzia testowe: sztuczne urządzenia oparte na plikach, fałszywy backend, konsola ze skryptem."""
import io
import os
import struct
import tempfile
import uuid
import zlib

from media_tool import logs
from media_tool.backends.base import Backend
from media_tool.models import Device, Partition
from media_tool.ui import Console


def make_file(path, size, seed=7):
    """Plik z powtarzalną, niepowtarzalną na blokach zawartością (pseudolosową)."""
    import random
    r = random.Random(seed)
    with open(path, "wb") as f:
        left = size
        while left > 0:
            n = min(left, 65536)
            f.write(bytes(r.getrandbits(8) for _ in range(256)) * (n // 256) + b"\x01" * (n % 256))
            left -= n
    return path


def make_gpt_image(path, sectors=2048, sector=512):
    size = sectors * sector
    img = bytearray(size)
    img[446 + 4] = 0xEE
    struct.pack_into("<II", img, 446 + 8, 1, sectors - 1)
    img[510:512] = b"\x55\xaa"
    n, esz = 128, 128
    entries = bytearray(n * esz)
    struct.pack_into("<16s16sQQQ", entries, 0, uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7").bytes_le,
                     uuid.UUID("11111111-2222-3333-4444-555555555555").bytes_le, 34, 1000, 0)
    nm = "DATA".encode("utf-16le")
    entries[56:56 + len(nm)] = nm
    ecrc = zlib.crc32(bytes(entries))
    guid = uuid.UUID("99999999-8888-7777-6666-555555555555").bytes_le

    def header(cur, backup, ent_lba):
        h = bytearray(92)
        h[0:8] = b"EFI PART"
        struct.pack_into("<IIII", h, 8, 0x10000, 92, 0, 0)
        struct.pack_into("<QQQQ", h, 24, cur, backup, 34, sectors - 34)
        h[56:72] = guid
        struct.pack_into("<QIII", h, 72, ent_lba, n, esz, ecrc)
        struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)))
        return bytes(h)
    img[sector:sector + 92] = header(1, sectors - 1, 2)
    img[2 * sector:2 * sector + len(entries)] = entries
    bl = (sectors - 33) * sector
    img[bl:bl + len(entries)] = entries
    img[(sectors - 1) * sector:(sectors - 1) * sector + 92] = header(sectors - 1, 1, sectors - 33)
    with open(path, "wb") as f:
        f.write(img)
    return path


def scripted_console(inputs=None, assume_yes=False):
    it = iter(inputs or [])
    out, err = io.StringIO(), io.StringIO()

    def fake_input(prompt=""):
        err.write(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    c = Console(input_fn=fake_input, out=out, err=err, assume_yes=assume_yes)
    c.captured_out, c.captured_err = out, err
    return c


def make_device(path="/dev/fake0", size=1048576, serial="SN12345", mounts=None, system=False, **kw):
    d = Device(path=path, system_name=path.split("/")[-1], vendor="Acme", model="TestCard", serial=serial,
               size_detected_bytes=size, size_nominal_bytes=size, sector_size=512, device_type="karta SD",
               interface="SD", is_system=system, removable=True, internal=False, read_only=False,
               mount_points=mounts if mounts is not None else [], mounted=bool(mounts), partition_table="GPT")
    d.partitions = [Partition(identifier="fake0s1", path=path + "s1", size_bytes=size // 2, fs_type="exfat",
                              volume_label="CARD", fs_uuid="ABCD-1234", partuuid="p-uuid-1",
                              mount_points=mounts or [])]
    for k, v in kw.items():
        setattr(d, k, v)
    return d


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, devices, raw=None):
        self.devices = devices
        self.raw = raw
        self.calls = []

    def list_devices(self, include_virtual=False):
        return list(self.devices)

    def raw_path(self, device):
        return self.raw or device.path

    def unmount(self, device):
        self.calls.append(("unmount", device.path))
        return ["unmounted"]

    def power_off(self, device):
        self.calls.append(("power_off", device.path))
        return "powered off"

    def read_health(self, device):
        from media_tool.models import Health
        return Health(status="PASSED", temperature_c=30, power_on_hours=10, error_count=0, source="fake")


class IsolatedHome:
    """Izoluje config/logi w katalogu tymczasowym."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._old = os.environ.get("MEDIA_TOOL_HOME")
        os.environ["MEDIA_TOOL_HOME"] = os.path.join(self.tmp, "home")
        logs.reset_for_tests()

    def tearDown(self):
        logs.reset_for_tests()
        if self._old is None:
            os.environ.pop("MEDIA_TOOL_HOME", None)
        else:
            os.environ["MEDIA_TOOL_HOME"] = self._old
        self._tmp.cleanup()

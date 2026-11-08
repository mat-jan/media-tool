"""Parsery backendów na danych w formacie prawdziwych narzędzi (macOS / Linux / Windows)."""
import unittest

from media_tool.backends.linux import LinuxBackend
from media_tool.backends.macos import index_profiler, system_disks
from media_tool.backends.windows import WindowsBackend

LSBLK = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 500107862016, "model": "Samsung SSD 860", "vendor": "ATA     ",
     "serial": "S3Z9NB0K", "rev": "4B6Q", "tran": "sata", "rm": False, "ro": False, "rota": False, "log-sec": 512,
     "phy-sec": 512, "pttype": "gpt", "wwn": "0x5002538e40a4f0a9", "ptuuid": "abc-ptuuid",
     "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 536870912, "fstype": "vfat", "label": "EFI",
          "uuid": "ABCD-1234", "partuuid": "pu-1", "parttype": "c12a7328", "mountpoints": ["/boot/efi"], "ro": False, "start": 2048},
         {"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 499000000000, "fstype": "ext4", "label": None,
          "uuid": "uuid-root", "partuuid": "pu-2", "mountpoints": ["/"], "ro": False, "start": 1050624}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 15931539456, "model": "Ultra", "vendor": "SanDisk ",
     "serial": "4C530001", "rev": "1.00", "tran": "usb", "rm": True, "ro": False, "rota": False, "log-sec": 512, "phy-sec": 512,
     "pttype": "dos", "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "size": 15930000000, "fstype": "exfat",
                                    "label": "USBSTICK", "uuid": "6A2B-1C3D", "partuuid": "12345678-01",
                                    "mountpoints": ["/media/user/USBSTICK"], "ro": False, "start": 2048}]},
    {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 63864569856, "model": None, "vendor": None,
     "serial": None, "tran": None, "rm": True, "ro": False, "rota": False, "log-sec": 512, "phy-sec": 512, "pttype": "dos",
     "children": [{"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part", "size": 63860000000, "fstype": "exfat",
                   "label": "SDCARD", "uuid": "1111-2222", "partuuid": "aaaa-01", "mountpoints": [None], "ro": False, "start": 8192}]},
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 1000, "mountpoints": ["/snap/core"]},
]}


class LinuxParserTests(unittest.TestCase):
    def setUp(self):
        b = LinuxBackend()
        b.sys_root = "/nonexistent-sys"       # brak sysfs/udev w testach
        self.devs = {d.system_name: d for d in b.parse_lsblk(LSBLK)}

    def test_filters_virtual_devices(self):
        self.assertEqual(set(self.devs), {"sda", "sdb", "mmcblk0"})

    def test_internal_system_disk(self):
        d = self.devs["sda"]
        self.assertTrue(d.is_system)
        self.assertEqual(d.serial, "S3Z9NB0K")
        self.assertEqual(d.wwn, "0x5002538e40a4f0a9")
        self.assertEqual((d.vendor, d.interface, d.device_type, d.partition_table), ("ATA", "SATA", "SSD", "GPT"))
        self.assertEqual([p.mount_points for p in d.partitions], [["/boot/efi"], ["/"]])
        self.assertEqual(d.partitions[0].fs_uuid, "ABCD-1234")
        self.assertEqual(d.partitions[0].partuuid, "pu-1")

    def test_usb_stick(self):
        d = self.devs["sdb"]
        self.assertFalse(d.is_system)
        self.assertTrue(d.removable)
        self.assertEqual((d.interface, d.partition_table, d.mounted), ("USB", "MBR", True))
        self.assertEqual(d.mount_points, ["/media/user/USBSTICK"])
        self.assertTrue(any("interfejsu USB" in n for n in d.notes))
        self.assertEqual(d.volume_label, "USBSTICK")

    def test_native_sd_card_has_card_serial_slot_and_no_fake_device_serial(self):
        d = self.devs["mmcblk0"]
        self.assertEqual(d.device_type, "karta SD/MMC")
        self.assertIsNone(d.serial)               # nie zgadujemy
        self.assertFalse(d.mounted)               # [None] nie jest punktem montowania
        self.assertEqual(d.mount_points, [])


MAC_PROFILER = {
    "SPUSBDataType": [{"_name": "USB31Bus", "_items": [
        {"_name": "Hub", "vendor_id": "0x05e3", "product_id": "0x0610", "serial_num": "HUBSERIAL", "_items": [
            {"_name": "Card Reader", "vendor_id": "0x0bda (Realtek)", "product_id": "0x0316", "bcd_device": "3.07",
             "manufacturer": "Realtek", "serial_num": "READER123",
             "Media": [{"_name": "SD Card", "bsd_name": "disk4", "size_in_bytes": 63864569856}]},
            {"_name": "Stick", "vendor_id": "0x0781 (SanDisk)", "product_id": "0x5581", "manufacturer": "SanDisk",
             "Media": [{"_name": "Ultra", "bsd_name": "disk5"}]}]}]}],
    "SPNVMeDataType": [{"_name": "Apple SSD Controller", "_items": [
        {"_name": "APPLE SSD", "bsd_name": "disk0", "device_serial": "NVMESERIAL", "spnvme_trim_support": "Yes"}]}],
}


class MacParserTests(unittest.TestCase):
    def test_profiler_index_uses_nearest_usb_device_not_hub(self):
        idx = index_profiler(MAC_PROFILER)
        self.assertEqual(idx["disk4"]["usb"]["serial_num"], "READER123")
        # nośnik bez własnego numeru nie może dziedziczyć numeru huba
        self.assertNotIn("serial_num", idx["disk5"]["usb"])
        self.assertEqual(idx["disk5"]["usb"]["product_id"], "0x5581")
        self.assertEqual(idx["disk0"]["self"]["device_serial"], "NVMESERIAL")

    def test_system_disk_detection(self):
        entries = [
            {"DeviceIdentifier": "disk0", "Partitions": [{"DeviceIdentifier": "disk0s2"}]},
            {"DeviceIdentifier": "disk3", "APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
             "APFSVolumes": [{"DeviceIdentifier": "disk3s1", "MountPoint": "/"}]},
            {"DeviceIdentifier": "disk4", "Partitions": [{"DeviceIdentifier": "disk4s1", "MountPoint": "/Volumes/CARD"}]}]
        self.assertEqual(system_disks(entries), {"disk0"})


PS_DISKS = [
    {"Number": 0, "FriendlyName": "Samsung SSD 970", "Manufacturer": "(Standardowe dyski)", "Model": "Samsung SSD 970 EVO",
     "Serial": "S4EVNX0M", "Firmware": "2B2QEXE7", "Size": 500107862016, "LogicalSectorSize": 512, "PhysicalSectorSize": 512,
     "PartitionStyle": "GPT", "IsSystem": True, "IsBoot": True, "IsReadOnly": False, "BusType": "NVMe",
     "UniqueId": "eui.0025385b21404c56", "Guid": "{aaaa}", "MediaType": "SSD", "Pnp": "SCSI\\DISK", "Parent": None,
     "InterfaceType": "SCSI",
     "Partitions": [{"Number": 1, "Size": 100000000, "Offset": 1048576, "Guid": "{p1}", "GptType": "{c12a7328}",
                     "AccessPaths": [], "FileSystem": "FAT32", "Label": "SYSTEM", "VolumeId": "\\\\?\\Volume{v1}\\",
                     "VolumeSerial": None, "DriveLetter": ""},
                    {"Number": 2, "Size": 400000000000, "Offset": 200000000, "Guid": "{p2}", "GptType": "{ebd0a0a2}",
                     "AccessPaths": ["C:\\"], "FileSystem": "NTFS", "Label": "Windows", "VolumeId": "\\\\?\\Volume{v2}\\",
                     "VolumeSerial": "5A3C9E17", "DriveLetter": "C"}]},
    {"Number": 2, "FriendlyName": "Generic STORAGE DEVICE", "Manufacturer": "Generic", "Model": "Generic STORAGE DEVICE USB Device",
     "Serial": "000000001", "Firmware": None, "Size": 63864569856, "LogicalSectorSize": 512, "PhysicalSectorSize": 512,
     "PartitionStyle": "MBR", "IsSystem": False, "IsBoot": False, "IsReadOnly": False, "BusType": "USB", "UniqueId": None,
     "MediaType": "Unspecified", "Pnp": "USBSTOR\\DISK&VEN_GENERIC", "Parent": "USB\\VID_058F&PID_6387\\1A2B3C4D",
     "InterfaceType": "USB",
     "Partitions": {"Number": 1, "Size": 63000000000, "Offset": 1048576, "Guid": None, "MbrType": 7, "AccessPaths": ["E:\\"],
                    "FileSystem": "exFAT", "Label": "SDCARD", "VolumeId": None, "VolumeSerial": "ABCD1234", "DriveLetter": "E"}},
]


class WindowsParserTests(unittest.TestCase):
    def setUp(self):
        self.devs = {d.system_name: d for d in WindowsBackend().parse_disks(PS_DISKS)}

    def test_system_nvme(self):
        d = self.devs["PhysicalDrive0"]
        self.assertEqual(d.path, "\\\\.\\PHYSICALDRIVE0")
        self.assertTrue(d.is_system)
        self.assertEqual((d.eui64, d.partition_table, d.serial), ("0025385b21404c56", "GPT", "S4EVNX0M"))
        self.assertEqual(d.partitions[1].fs_uuid, "5A3C9E17")
        self.assertEqual(d.partitions[1].mount_points, ["C:\\"])
        self.assertEqual(d.mount_points, ["C:\\"])

    def test_usb_reader_vid_pid_and_single_partition_object(self):
        d = self.devs["PhysicalDrive2"]
        self.assertEqual((d.usb_vid, d.usb_pid), ("058F", "6387"))
        self.assertTrue(d.removable and not d.is_system)
        self.assertEqual(len(d.partitions), 1)      # PowerShell zwraca pojedynczy obiekt zamiast listy
        self.assertEqual(d.mount_points, ["E:\\"])
        self.assertIsNone(d.wwn)
        self.assertTrue(any("interfejsu USB" in n for n in d.notes))


if __name__ == "__main__":
    unittest.main()

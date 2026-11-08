"""Surowe urządzenia Windows/macOS odrzucają odczyty niewyrównane do sektora – symulujemy to."""
import io
import os
import unittest
from unittest import mock

from media_tool import scanning
from media_tool.rawio import RawReader, can_read
from media_tool.util import ensure_utf8_streams
from tests.helpers import IsolatedHome, make_file, make_gpt_image


def strict(sector):
    orig = RawReader.read_at

    def read_at(self, offset, length):
        if offset % sector or length % sector:
            raise OSError(22, "Invalid argument (odczyt niewyrównany do sektora)")
        return orig(self, offset, length)
    return mock.patch.object(RawReader, "read_at", read_at)


class AlignmentTests(IsolatedHome, unittest.TestCase):
    def test_read_aligned_returns_exact_bytes_from_unaligned_request(self):
        p = make_file(os.path.join(self.tmp, "d"), 64 * 1024)
        with open(p, "rb") as f:
            raw = f.read()
        with strict(4096), RawReader(p) as rd:
            self.assertEqual(rd.read_aligned(5000, 300, 4096), raw[5000:5300])
            self.assertEqual(rd.read_aligned(0, 10, 4096), raw[:10])
            self.assertEqual(rd.read_aligned(4090, 20, 4096), raw[4090:4110])   # przez granicę sektora
            with self.assertRaises(OSError):
                rd.read_at(5000, 300)                                           # surowy odczyt jest ścisły

    def test_read_aligned_swallows_io_errors(self):
        p = make_file(os.path.join(self.tmp, "d"), 8192)
        with RawReader(p) as rd, mock.patch.object(RawReader, "read_at", side_effect=OSError(5, "EIO")):
            self.assertEqual(rd.read_aligned(0, 512, 512), b"")

    def test_integrity_on_4k_sector_device_reads_partition_table_and_aligns_ranges(self):
        p = make_gpt_image(os.path.join(self.tmp, "gpt4k.img"), sectors=512, sector=4096)
        size = os.path.getsize(p)
        with strict(4096):
            res = scanning.integrity(p, size, sector=4096, areas=[(100, 5000)], passes=2, seed=1)
        self.assertTrue(res["passed"], res)                          # niewyrównany zakres nie zrobił "uszkodzonych sektorów"
        self.assertEqual(res["areas"][0]["offset"], 0)
        self.assertEqual(res["areas"][0]["length"], 8192)           # 100..5100 -> sektory 4096-bajtowe
        self.assertEqual(res["partition_table"]["scheme"], "GPT")
        self.assertEqual(res["partition_table"]["sector_size"], 4096)

    def test_default_integrity_and_scan_work_on_strict_device(self):
        p = make_gpt_image(os.path.join(self.tmp, "g.img"), sectors=4096)
        size = os.path.getsize(p)
        with strict(512):
            self.assertTrue(scanning.integrity(p, size, 512, samples=5, seed=3)["passed"])
            self.assertTrue(scanning.scan(p, size, 512, chunk_size=65536)["clean"])


class MiscPortabilityTests(IsolatedHome, unittest.TestCase):
    def test_can_read(self):
        p = make_file(os.path.join(self.tmp, "f"), 100)
        self.assertTrue(can_read(p))
        self.assertFalse(can_read(os.path.join(self.tmp, "nie-ma")))

    def test_utf8_streams_survive_legacy_console_encoding(self):
        raw = io.BytesIO()
        legacy = io.TextIOWrapper(raw, encoding="cp1250", errors="strict")
        with mock.patch("sys.stdout", legacy), mock.patch("sys.stderr", legacy):
            ensure_utf8_streams()
            print("✔ dostępne – zażółć gęślą jaźń")       # w cp1250 symbol ✔ rzuciłby UnicodeEncodeError
            legacy.flush()
        self.assertIn("✔".encode("utf-8"), raw.getvalue())


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(__import__("sys").platform == "win32", "chown/geteuid nie istnieją na Windows")
class SudoOwnershipTests(IsolatedHome, unittest.TestCase):
    def test_files_and_new_dirs_go_to_invoking_user_under_sudo(self):
        from media_tool.util import atomic_write_text
        target = os.path.join(self.tmp, "new", "deeper", "r.txt")
        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch.dict(os.environ, {"SUDO_UID": "501", "SUDO_GID": "20"}), \
             mock.patch("os.chown") as chown:
            atomic_write_text(target, "x")
        chowned = [c.args[0] for c in chown.call_args_list]
        self.assertIn(target, chowned)
        self.assertIn(os.path.join(self.tmp, "new"), chowned)
        self.assertIn(os.path.join(self.tmp, "new", "deeper"), chowned)
        self.assertTrue(all(c.args[1:] == (501, 20) for c in chown.call_args_list))

    def test_no_chown_without_sudo_or_for_non_root(self):
        from media_tool.util import chown_to_invoking_user
        with mock.patch("os.chown") as chown:
            with mock.patch("os.geteuid", return_value=501, create=True), \
                 mock.patch.dict(os.environ, {"SUDO_UID": "501", "SUDO_GID": "20"}):
                chown_to_invoking_user("/x")
            with mock.patch("os.geteuid", return_value=0, create=True), \
                 mock.patch.dict(os.environ, {"SUDO_UID": "evil; rm", "SUDO_GID": "20"}):
                chown_to_invoking_user("/x")
        chown.assert_not_called()


class PathResolutionTests(IsolatedHome, unittest.TestCase):
    @unittest.skipIf(__import__("sys").platform == "win32", "skrypt sh / bit wykonywalny – tylko POSIX")
    def test_tools_are_found_even_with_minimal_path(self):
        """sudo / uruchomienie z GUI często ma PATH bez /usr/sbin, gdzie leży np. diskutil."""
        from media_tool import util
        tooldir = os.path.join(self.tmp, "sbin")
        os.makedirs(tooldir)
        exe = os.path.join(tooldir, "fake-tool")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\necho found-it\n")
        os.chmod(exe, 0o755)
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), \
             mock.patch.dict(util._EXTRA_DIRS, {"darwin": [tooldir], "linux": [tooldir]}):
            self.assertEqual(util.which("fake-tool"), exe)
            if os.name != "nt":
                r = util.run(["fake-tool"])
                self.assertTrue(r.ok and r.out.strip() == "found-it", r)
            self.assertIsNone(util.which("definitely-not-installed-xyz"))

    @unittest.skipUnless(__import__("sys").platform == "darwin", "tylko macOS")
    def test_diskutil_found_with_minimal_path_on_macos(self):
        from media_tool import util
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            self.assertTrue(util.which("diskutil"))


class CoarseClockTests(IsolatedHome, unittest.TestCase):
    def test_benchmark_and_scan_report_nonzero_speed_when_clock_does_not_advance(self):
        """Windows (Python <= 3.12) ma zegar o rozdzielczości ~15 ms: krótki odczyt trwa "0 s"."""
        from media_tool import benchmark
        p = make_file(os.path.join(self.tmp, "dev"), 4 * 1024 * 1024)
        size = os.path.getsize(p)
        with mock.patch("time.perf_counter", return_value=100.0):
            b = benchmark.benchmark_raw(p, size, 512, mib=4, random_ops=20)
            sc = scanning.scan(p, size, 512, chunk_size=65536)
        self.assertGreater(b["seq_avg_mb_s"], 0)
        self.assertTrue(all(s["mb_s"] > 0 for s in b["sequential"]))
        self.assertGreater(sc["avg_mb_s"], 0)

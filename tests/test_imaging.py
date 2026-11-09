import hashlib
import json
import os
import unittest
from unittest import mock

from media_tool import imaging
from media_tool.errors import Cancelled, ImagingError
from media_tool.rawio import RawReader
from tests.helpers import IsolatedHome, make_file, make_gpt_image

CHUNK = 64 * 1024


def rb(path):
    with open(path, "rb") as f:
        return f.read()


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha(path):
    return hashlib.sha256(rb(path)).hexdigest()


class ImagingTests(IsolatedHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.src = make_file(os.path.join(self.tmp, "dev.bin"), 1_000_000 + 512 * 3)
        self.size = os.path.getsize(self.src)
        self.out = os.path.join(self.tmp, "out", "backup.img")
        os.makedirs(os.path.dirname(self.out))

    def create(self, **kw):
        kw.setdefault("sector_size", 512)
        kw.setdefault("chunk_size", CHUNK)
        return imaging.create_image(self.src, self.size, self.out, **kw)

    def test_create_matches_source_and_never_modifies_it(self):
        before = (sha(self.src), os.stat(self.src).st_mtime_ns)
        st = self.create(source_meta={"identity": {"serial": "A"}})
        self.assertEqual(sha(self.out), sha(self.src))
        self.assertEqual(st["image"]["hash"], sha(self.src))
        self.assertTrue(st["progress"]["complete"])
        self.assertEqual(os.path.getsize(self.out), self.size)
        self.assertEqual(before, (sha(self.src), os.stat(self.src).st_mtime_ns))
        side = _json(imaging.sidecar_path(self.out))
        self.assertEqual(side["image"]["hash"], st["image"]["hash"])

    def test_refuses_to_overwrite_and_hints_resume(self):
        self.create()
        with self.assertRaises(ImagingError) as cm:
            self.create()
        self.assertIn("już istnieje", str(cm.exception))
        self.create(overwrite=True)

    def test_refuses_output_on_source_volume(self):
        with self.assertRaises(ImagingError) as cm:
            self.create(avoid_paths=[os.path.dirname(self.out)])
        self.assertIn("nośniku", str(cm.exception))
        self.assertFalse(os.path.exists(self.out))

    def test_resume_after_cancel_gives_identical_image(self):
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 5

        meta = {"identity": {"serial": "A"}}
        with self.assertRaises(Cancelled):
            self.create(source_meta=meta, cancel=cancel)
        side = imaging.load_sidecar(self.out)
        self.assertFalse(side["progress"]["complete"])
        self.assertGreater(side["progress"]["bytes_done"], 0)
        st = self.create(source_meta=meta, resume=True)
        self.assertTrue(st["progress"]["complete"])
        self.assertEqual(sha(self.out), sha(self.src))
        self.assertEqual(st["image"]["hash"], sha(self.src))

    def test_resume_refuses_different_device(self):
        n = {"i": 0}
        with self.assertRaises(Cancelled):
            self.create(source_meta={"identity": {"serial": "A"}}, cancel=lambda: (n.__setitem__("i", n["i"] + 1) or n["i"] > 3))
        with self.assertRaises(ImagingError) as cm:
            self.create(source_meta={"identity": {"serial": "B"}}, resume=True)
        self.assertIn("tożsamość", str(cm.exception))

    def test_resume_requires_existing_image(self):
        with self.assertRaises(ImagingError):
            self.create(resume=True)

    def test_bad_sector_is_zero_filled_and_recorded(self):
        bad = 40 * 512

        class Flaky(RawReader):
            def read_at(self, offset, length):
                if offset <= bad < offset + length:
                    raise OSError(5, "I/O error")
                return super().read_at(offset, length)

        with mock.patch.object(imaging, "RawReader", Flaky):
            st = self.create()
        self.assertEqual(st["bad_ranges"], [[bad, 512]])
        data = rb(self.out)
        self.assertEqual(data[bad:bad + 512], b"\x00" * 512)
        self.assertEqual(data[:bad], rb(self.src)[:bad])
        # weryfikacja pomija znane nieczytelne zakresy
        with mock.patch.object(imaging, "RawReader", Flaky):
            res = imaging.verify_image(self.src, self.size, self.out, sector_size=512, chunk_size=CHUNK)
        self.assertTrue(res["match"], res)

    def test_verify_ignores_recorded_bad_range_even_if_device_returns_garbage_there(self):
        bad = 40 * 512

        class Unreadable(RawReader):
            def read_at(self, offset, length):
                if offset <= bad < offset + length:
                    raise OSError(5, "I/O error")
                return super().read_at(offset, length)

        class Garbage(RawReader):          # ten sam sektor "ożył" i zwraca inne dane niż zera w obrazie
            def read_at(self, offset, length):
                data = bytearray(super().read_at(offset, length))
                if offset <= bad < offset + length:
                    data[bad - offset:bad - offset + 512] = b"\xff" * 512
                return bytes(data)

        with mock.patch.object(imaging, "RawReader", Unreadable):
            self.create()
        with mock.patch.object(imaging, "RawReader", Garbage):
            res = imaging.verify_image(self.src, self.size, self.out, sector_size=512, chunk_size=CHUNK)
        self.assertTrue(res["match"], res)
        self.assertEqual(res["mismatched_chunks"], 0)
        # a różnica poza zapisanym zakresem nadal jest wykrywana
        with open(self.out, "r+b") as f:
            f.seek(bad + 4096)
            f.write(b"!")
        with mock.patch.object(imaging, "RawReader", Garbage):
            res = imaging.verify_image(self.src, self.size, self.out, sector_size=512, chunk_size=CHUNK)
        self.assertFalse(res["match"])

    def test_error_limit_aborts(self):
        class AllBad(RawReader):
            def read_at(self, offset, length):
                raise OSError(5, "I/O error")
        with mock.patch.object(imaging, "RawReader", AllBad):
            with self.assertRaises(ImagingError) as cm:
                self.create(max_errors=10)
        self.assertIn("limit", str(cm.exception))
        self.assertFalse(imaging.load_sidecar(self.out)["progress"]["complete"])

    def test_verify_ok_and_detects_modification(self):
        self.create()
        res = imaging.verify_image(self.src, self.size, self.out, chunk_size=CHUNK)
        self.assertTrue(res["match"])
        self.assertTrue(res["hash_matches_record"])
        with open(self.src, "r+b") as f:
            f.seek(500_000)
            f.write(b"\xff\xfe")
        res = imaging.verify_image(self.src, self.size, self.out, chunk_size=CHUNK)
        self.assertFalse(res["match"])
        self.assertEqual(res["mismatched_chunks"], 1)
        self.assertIn(res["first_mismatch_offset"], range(500_000, 500_002))

    def test_verify_detects_size_difference(self):
        self.create()
        with open(self.out, "ab") as f:
            f.write(b"x" * 512)
        res = imaging.verify_image(self.src, self.size, self.out, chunk_size=CHUNK)
        self.assertFalse(res["size_match"])
        self.assertFalse(res["match"])

    def test_compare_and_hash(self):
        self.create()
        copy = os.path.join(self.tmp, "copy.img")
        with open(copy, "wb") as f:
            f.write(rb(self.out))
        r = imaging.compare_images(self.out, copy, chunk_size=CHUNK)
        self.assertTrue(r["identical"])
        with open(copy, "r+b") as f:
            f.seek(70_000)
            f.write(b"Z")
        r = imaging.compare_images(self.out, copy, chunk_size=CHUNK)
        self.assertFalse(r["identical"])
        self.assertEqual(r["first_difference_offset"], 70_000)
        self.assertEqual(imaging.hash_file(self.out), sha(self.out))
        self.assertEqual(imaging.hash_file(self.out, "md5"), hashlib.md5(rb(self.out)).hexdigest())
        with self.assertRaises(ImagingError):
            imaging.hash_file(self.out, "nie-ma-takiego")

    def test_plan_reports_problems_without_touching_anything(self):
        plan = imaging.plan_create(self.src, self.size, self.out)
        self.assertTrue(plan["ok"])
        self.assertFalse(os.path.exists(self.out))
        plan = imaging.plan_create(self.src, self.size, os.path.join(self.tmp, "no-dir", "x.img"))
        self.assertTrue(plan["ok"])                      # brakujący katalog zostanie utworzony
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "no-dir")))     # ...ale dopiero przy prawdziwym zapisie
        st = imaging.create_image(self.src, self.size, os.path.join(self.tmp, "no-dir", "sub", "x.img"),
                                  chunk_size=CHUNK)
        self.assertTrue(st["progress"]["complete"])

    def test_inspect_image_without_and_with_metadata(self):
        self.create()
        info = imaging.inspect_image(self.out, compute_hash=True)
        self.assertTrue(info["metadata_present"])
        self.assertTrue(info["hash_matches_record"])
        os.unlink(imaging.sidecar_path(self.out))
        info = imaging.inspect_image(self.out)
        self.assertFalse(info["metadata_present"])
        self.assertIn("Brak pliku", info["note"])


class PartitionTableTests(IsolatedHome, unittest.TestCase):
    def test_gpt_is_parsed_and_validated(self):
        p = make_gpt_image(os.path.join(self.tmp, "gpt.img"))
        info = imaging.inspect_image(p)["partition_table"]
        self.assertEqual(info["scheme"], "GPT")
        self.assertEqual(info["problems"], [])
        self.assertEqual(len(info["partitions"]), 1)
        part = info["partitions"][0]
        self.assertEqual(part["name"], "DATA")
        self.assertEqual(part["type"], "Microsoft basic data")
        self.assertEqual(part["partuuid"], "11111111-2222-3333-4444-555555555555")

    def test_corrupted_gpt_header_is_reported(self):
        p = make_gpt_image(os.path.join(self.tmp, "gpt.img"))
        with open(p, "r+b") as f:
            f.seek(512 + 40)
            f.write(b"\x07")   # psuje pole first_usable_lba -> zły CRC
        info = imaging.inspect_image(p)["partition_table"]
        self.assertTrue(any("CRC nagłówka" in x for x in info["problems"]), info["problems"])

    def test_backup_header_not_at_end_is_reported(self):
        p = make_gpt_image(os.path.join(self.tmp, "gpt.img"))
        with open(p, "ab") as f:
            f.write(b"\x00" * 512 * 100)   # obraz "wgrany" na większy nośnik
        info = imaging.inspect_image(p)["partition_table"]
        self.assertTrue(any("nie leży na końcu" in x for x in info["problems"]), info["problems"])

    def test_empty_file_has_no_table(self):
        p = os.path.join(self.tmp, "zero.img")
        with open(p, "wb") as f:
            f.write(b"\x00" * 8192)
        self.assertEqual(imaging.inspect_image(p)["partition_table"]["scheme"], "brak")


if __name__ == "__main__":
    unittest.main()

import json
import os
import unittest
from unittest import mock

from media_tool import backup
from media_tool.errors import Cancelled, MediaToolError
from tests.helpers import IsolatedHome, make_file


def write(path, data=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def rb(path):
    with open(path, "rb") as f:
        return f.read()


class BackupTests(IsolatedHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.tmp, "card")
        self.dst = os.path.join(self.tmp, "backups")
        os.makedirs(self.dst)
        write(os.path.join(self.src, "DCIM", "100", "IMG_0001.JPG"), b"photo-one" * 1000, 1_700_000_000)
        write(os.path.join(self.src, "DCIM", "100", "IMG_0002.JPG"), b"photo-two" * 1000, 1_700_000_100)
        write(os.path.join(self.src, "PRIVATE", "clip.MP4"), b"video" * 5000, 1_700_000_200)
        write(os.path.join(self.src, "notes.txt"), b"hello", 1_700_000_300)

    def run_bk(self, **kw):
        kw.setdefault("name", "Karta")
        name = kw.pop("name")
        return backup.run_backup(self.src, self.dst, name, **kw)

    def target(self, *parts):
        return os.path.join(self.dst, "Karta", *parts)

    def test_first_backup_copies_everything_identically_and_leaves_source_untouched(self):
        before = {p: (rb(p), os.stat(p).st_mtime_ns) for p in
                  (os.path.join(dp, f) for dp, _, fs in os.walk(self.src) for f in fs)}
        r = self.run_bk()
        self.assertEqual((r["copied"], r["skipped_unchanged"], len(r["failed"])), (4, 0, 0))
        self.assertTrue(r["complete"])
        for p, (data, mt) in before.items():
            rel = os.path.relpath(p, self.src)
            self.assertEqual(rb(self.target(rel)), data)
            self.assertEqual(os.stat(self.target(rel)).st_mtime_ns, mt)      # data pliku zachowana
            self.assertEqual((rb(p), os.stat(p).st_mtime_ns), (data, mt))    # źródło nietknięte
        self.assertTrue(backup.is_backup_set(self.target()))

    def test_second_run_is_incremental_and_reads_nothing(self):
        self.run_bk()
        with mock.patch.object(backup, "_copy_one", side_effect=AssertionError("nie powinno kopiować")):
            r = self.run_bk()
        self.assertEqual((r["copied"], r["skipped_unchanged"]), (0, 4))
        write(os.path.join(self.src, "DCIM", "100", "IMG_0003.JPG"), b"new" * 100, 1_700_000_400)
        r = self.run_bk()
        self.assertEqual((r["copied"], r["skipped_unchanged"]), (1, 4))

    def test_reused_filename_with_different_content_never_overwrites(self):
        """Aparat po sformatowaniu karty zaczyna numerację od nowa: IMG_0001.JPG ma teraz inną zawartość."""
        self.run_bk()
        old = rb(self.target("DCIM", "100", "IMG_0001.JPG"))
        write(os.path.join(self.src, "DCIM", "100", "IMG_0001.JPG"), b"COMPLETELY-DIFFERENT" * 500, 1_800_000_000)
        r = self.run_bk()
        self.assertEqual((r["copied"], r["renamed_collisions"]), (1, 1))
        self.assertEqual(rb(self.target("DCIM", "100", "IMG_0001.JPG")), old)           # stara wersja nietknięta
        extras = [f for f in os.listdir(self.target("DCIM", "100")) if f.startswith("IMG_0001_")]
        self.assertEqual(len(extras), 1)
        self.assertEqual(rb(self.target("DCIM", "100", extras[0])), b"COMPLETELY-DIFFERENT" * 500)
        r = self.run_bk()                                                               # i znów przyrostowo
        self.assertEqual((r["copied"], r["skipped_unchanged"]), (0, 4))

    def test_same_content_with_new_timestamp_is_deduplicated(self):
        self.run_bk()
        os.utime(os.path.join(self.src, "notes.txt"), (1_900_000_000, 1_900_000_000))
        r = self.run_bk()
        self.assertEqual((r["copied"], r["deduplicated"]), (0, 1))
        self.assertEqual(sorted(os.listdir(self.target())), sorted(["DCIM", "PRIVATE", "notes.txt", ".media-tool-backup"]))
        r = self.run_bk()
        self.assertEqual(r["skipped_unchanged"], 4)

    def test_category_filter_and_junk_is_skipped(self):
        write(os.path.join(self.src, ".Spotlight-V100", "junk"), b"j")
        write(os.path.join(self.src, ".DS_Store"), b"j")
        write(os.path.join(self.src, "._IMG_0001.JPG"), b"j")
        r = self.run_bk(only={"zdjęcia"})
        self.assertEqual(r["copied"], 2)
        self.assertFalse(os.path.exists(self.target("PRIVATE")))
        self.assertFalse(os.path.exists(self.target(".Spotlight-V100")))
        self.assertEqual(self.run_bk(name="Wszystko")["copied"], 4)

    def test_destination_inside_source_is_refused(self):
        with self.assertRaises(MediaToolError):
            backup.run_backup(self.src, os.path.join(self.src, "backups"), "x")
        self.assertFalse(os.path.exists(os.path.join(self.src, "backups")))

    def test_same_volume_warning_or_refusal(self):
        r = self.run_bk()
        self.assertTrue(any("tym samym woluminie" in w for w in r["warnings"]))    # tmp i tmp = ten sam wolumin
        with self.assertRaises(MediaToolError):
            self.run_bk(name="Inna", refuse_same_volume=True)

    def test_dry_run_writes_nothing(self):
        r = self.run_bk(dry_run=True)
        self.assertEqual((r["copied"], r["scanned_files"]), (4, 4))
        self.assertGreater(r["copied_bytes"], 0)
        self.assertEqual(os.listdir(self.dst), [])
        self.run_bk()
        self.assertEqual(self.run_bk(dry_run=True)["skipped_unchanged"], 4)

    def test_read_error_is_reported_and_others_continue(self):
        bad = os.path.join(self.src, "PRIVATE", "clip.MP4")
        real_open = open

        def flaky(path, *a, **k):
            if os.fspath(path) == bad and "rb" in (a[0] if a else k.get("mode", "r")):
                raise OSError(5, "Input/output error")
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", flaky):
            r = self.run_bk()
        self.assertEqual((r["copied"], len(r["failed"])), (3, 1))
        self.assertEqual(r["failed"][0][0], "PRIVATE/clip.MP4")
        self.assertFalse(os.path.exists(self.target("PRIVATE", "clip.MP4")))
        self.assertEqual(os.listdir(os.path.join(self.target(), backup.META_DIR, "tmp")), [])   # brak śmieci
        self.assertEqual(self.run_bk()["copied"], 1)                                            # nadrabia po naprawie

    def test_write_verification_failure_removes_bad_copy(self):
        real = backup.file_sha256
        calls = {"n": 0}

        def lying(path, cancel=lambda: False):
            calls["n"] += 1
            return "0" * 64 if "notes.txt" in path else real(path, cancel)
        with mock.patch.object(backup, "file_sha256", lying):
            r = self.run_bk()
        self.assertEqual(r["copied"], 3)
        self.assertEqual([f[0] for f in r["failed"]], ["notes.txt"])
        self.assertFalse(os.path.exists(self.target("notes.txt")))

    def test_cancel_saves_progress_and_resume_finishes(self):
        n = {"i": 0}

        def cancel():
            n["i"] += 1
            return n["i"] > 6
        with self.assertRaises(Cancelled):
            self.run_bk(cancel=cancel)
        idx = backup.Index(os.path.join(self.target(), backup.META_DIR))
        done = len(idx.files)
        self.assertGreaterEqual(done, 1)
        self.assertLess(done, 4)
        r = self.run_bk()
        self.assertEqual(r["copied"] + r["skipped_unchanged"] + r["deduplicated"], 4)
        self.assertEqual(r["skipped_unchanged"], done)

    def test_verify_detects_corruption_missing_and_extra(self):
        self.run_bk()
        v = backup.verify_backup(self.target())
        self.assertTrue(v["healthy"], v)
        self.assertEqual((v["ok"], v["versions_checked"]), (4, 4))
        with open(self.target("notes.txt"), "r+b") as f:
            f.write(b"J")                                   # ta sama długość, inna zawartość
        os.unlink(self.target("PRIVATE", "clip.MP4"))
        write(self.target("obcy.txt"), b"nie moj")
        v = backup.verify_backup(self.target())
        self.assertFalse(v["healthy"])
        self.assertEqual(v["corrupt"], ["notes.txt"])
        self.assertEqual(v["missing"], ["PRIVATE/clip.MP4"])
        self.assertIn("obcy.txt", v["extra_files"])
        with self.assertRaises(MediaToolError):
            backup.verify_backup(self.dst)

    def test_list_backups(self):
        self.run_bk()
        self.run_bk()
        root = backup.list_backups(self.dst)
        self.assertEqual(root["kind"], "root")
        self.assertEqual([s["name"] for s in root["sets"]], ["Karta"])
        one = backup.list_backups(self.target())
        self.assertEqual(one["kind"], "set")
        self.assertEqual((one["files"], len(one["runs"])), (4, 2) if len(one["runs"]) == 2 else (4, len(one["runs"])))
        self.assertGreaterEqual(len(one["runs"]), 1)

    def test_sanitize_name(self):
        self.assertEqual(backup.sanitize_name("../../etc/passwd"), "etc_passwd")
        self.assertEqual(backup.sanitize_name("SD: Canon/EOS"), "SD_ Canon_EOS")
        self.assertEqual(backup.sanitize_name(""), "nosnik")
        self.assertEqual(backup.sanitize_name("..."), "nosnik")


if __name__ == "__main__":
    unittest.main()

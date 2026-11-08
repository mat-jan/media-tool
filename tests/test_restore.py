"""Przywracanie obrazu = jedyna operacja zapisu na nośnik. Testujemy głównie, kiedy ma ODMÓWIĆ."""
import hashlib
import json
import os
import unittest
from unittest import mock

from media_tool import cli, imaging
from media_tool.errors import ImagingError
from tests.helpers import FakeBackend, IsolatedHome, make_device, make_file, make_gpt_image, scripted_console

SIZE = 1024 * 1024


def rb(p):
    with open(p, "rb") as f:
        return f.read()


class RestoreCoreTests(IsolatedHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.src = make_gpt_image(os.path.join(self.tmp, "src.bin"), sectors=SIZE // 512)
        self.img = os.path.join(self.tmp, "b.img")
        imaging.create_image(self.src, SIZE, self.img, chunk_size=65536, source_meta={"identity": {"serial": "A"}})
        self.dev = os.path.join(self.tmp, "dev.bin")        # "nośnik" docelowy = zwykły plik
        make_file(self.dev, SIZE, seed=99)

    def restore(self, **kw):
        kw.setdefault("sector_size", 512)
        kw.setdefault("chunk_size", 65536)
        return imaging.restore_image(self.dev, kw.pop("target_size", SIZE), kw.pop("image", self.img), **kw)

    def test_restore_writes_exact_image_and_verifies(self):
        self.assertNotEqual(rb(self.dev), rb(self.src))
        r = self.restore()
        self.assertTrue(r["verified"])
        self.assertEqual(r["bytes_written"], SIZE)
        self.assertEqual(rb(self.dev), rb(self.src))

    def test_smaller_image_leaves_rest_of_device_untouched(self):
        big = os.path.join(self.tmp, "big.bin")
        make_file(big, 2 * SIZE, seed=5)
        before = rb(big)
        with mock.patch.object(imaging, "plan_restore", wraps=imaging.plan_restore):
            r = imaging.restore_image(big, 2 * SIZE, self.img, sector_size=512, chunk_size=65536)
        after = rb(big)
        self.assertTrue(r["verified"])
        self.assertEqual(after[:SIZE], rb(self.src))
        self.assertEqual(after[SIZE:], before[SIZE:])
        self.assertTrue(any("mniejszy niż nośnik" in w for w in r["warnings"]))

    def test_image_larger_than_device_is_refused_and_nothing_written(self):
        before = rb(self.dev)
        with self.assertRaises(ImagingError) as cm:
            self.restore(target_size=SIZE // 2)
        self.assertIn("większy niż nośnik", str(cm.exception))
        self.assertEqual(rb(self.dev), before)

    def test_tampered_image_is_detected_before_any_write(self):
        before = rb(self.dev)
        with open(self.img, "r+b") as f:
            f.seek(5000)
            f.write(b"TAMPERED")
        with self.assertRaises(ImagingError) as cm:
            self.restore()
        self.assertIn("Suma kontrolna", str(cm.exception))
        self.assertEqual(rb(self.dev), before)             # nośnik nietknięty

    def test_incomplete_image_is_refused(self):
        side_path = imaging.sidecar_path(self.img)
        with open(side_path, encoding="utf-8") as f:
            side = json.load(f)
        side["progress"]["complete"] = False
        with open(side_path, "w", encoding="utf-8") as f:
            json.dump(side, f)
        before = rb(self.dev)
        with self.assertRaises(ImagingError) as cm:
            self.restore()
        self.assertIn("niekompletny", str(cm.exception))
        self.assertEqual(rb(self.dev), before)

    def test_image_without_metadata_needs_explicit_flag(self):
        os.unlink(imaging.sidecar_path(self.img))
        before = rb(self.dev)
        with self.assertRaises(ImagingError) as cm:
            self.restore()
        self.assertIn(".mtinfo.json", str(cm.exception))
        self.assertEqual(rb(self.dev), before)
        r = self.restore(allow_unverified_image=True)
        self.assertTrue(r["verified"])
        self.assertTrue(any("bez metadanych" in w for w in r["warnings"]))

    def test_image_not_multiple_of_sector_is_refused(self):
        odd = os.path.join(self.tmp, "odd.img")
        with open(odd, "wb") as f:
            f.write(b"x" * 1000)
        with self.assertRaises(ImagingError):
            imaging.restore_image(self.dev, SIZE, odd, sector_size=512, allow_unverified_image=True)

    def test_image_stored_on_target_volume_is_refused(self):
        with self.assertRaises(ImagingError) as cm:
            self.restore(avoid_paths=[os.path.dirname(self.img)])
        self.assertIn("na nośniku docelowym", str(cm.exception))

    def test_write_error_is_reported_as_inconsistent_state(self):
        real_open = open

        class Failing:
            def __init__(self, f):
                self.f, self.n = f, 0

            def __getattr__(self, k):
                return getattr(self.f, k)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def write(self, data):
                self.n += 1
                if self.n == 3:
                    raise OSError(28, "No space left on device")
                return self.f.write(data)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            return Failing(f) if path == self.dev and "+" in mode else f
        with mock.patch("builtins.open", fake_open):
            with self.assertRaises(ImagingError) as cm:
                self.restore()
        self.assertIn("niespójnym", str(cm.exception))

    def test_failed_verification_is_reported_not_hidden(self):
        bad = {"match": False, "mismatched_chunks": 3, "first_mismatch_offset": 4096}
        with mock.patch.object(imaging, "verify_image", return_value=bad):
            r = self.restore()
        self.assertFalse(r["verified"])
        self.assertEqual(r["first_mismatch_offset"], 4096)


class PlatformDecisionTests(unittest.TestCase):
    def test_restore_is_unavailable_only_on_windows(self):
        from media_tool import commands
        for platform, expected in (("win32", False), ("darwin", True), ("linux", True)):
            with mock.patch("media_tool.commands.sys.platform", platform):
                self.assertEqual(commands.restore_supported(), expected, platform)


class RestoreCliTests(IsolatedHome, unittest.TestCase):
    CONFIRM = ["/dev/fake0", "SN12345", "NADPISZ"]

    def setUp(self):
        super().setUp()
        self.src = make_gpt_image(os.path.join(self.tmp, "src.bin"), sectors=SIZE // 512)
        self.img = os.path.join(self.tmp, "b.img")
        imaging.create_image(self.src, SIZE, self.img, chunk_size=65536)
        self.raw = os.path.join(self.tmp, "raw.bin")
        make_file(self.raw, SIZE, seed=42)
        self.dev = make_device(size=SIZE, mounts=None)
        self.backend = FakeBackend([self.dev], raw=self.raw)
        # przywracanie istnieje tylko poza Windows – testy zachowania symulują taki system (też na CI z Windows)
        p = mock.patch("media_tool.commands.restore_supported", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv, inputs=None):
        con = scripted_console(inputs)
        with mock.patch.object(cli, "get_backend", return_value=self.backend):
            code = cli.main(list(argv), console=con)
        return code, con.captured_out.getvalue(), con.captured_err.getvalue()

    def test_confirmed_restore_writes_and_verifies(self):
        before = rb(self.raw)
        code, out, err = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=self.CONFIRM)
        self.assertEqual(code, 0, err)
        self.assertEqual(rb(self.raw), rb(self.src))
        self.assertNotEqual(rb(self.raw), before)
        self.assertIn("zweryfikowany", out)
        self.assertIn("NIEODWRACALNIE", err)          # ostrzeżenie pokazane przed pytaniem

    def test_every_wrong_confirmation_leaves_device_untouched(self):
        before = rb(self.raw)
        for inputs in (["/dev/fake0", "SN12345", "POTWIERDZAM"],      # słowo od innych operacji NIE wystarcza
                       ["/dev/fake0", "SN12345", "nadpisz"], ["/dev/fake0", "ZLY", "NADPISZ"],
                       ["fake0", "SN12345", "NADPISZ"], ["/dev/fake0", "SN12345", "tak"], []):
            code, _, _ = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=inputs)
            self.assertNotEqual(code, 0, inputs)
            self.assertEqual(rb(self.raw), before, inputs)

    def test_yes_flag_cannot_unlock_a_write(self):
        before = rb(self.raw)
        code, _, _ = self.run_cli("image", "restore", "fake0", "--image", self.img, "--yes")
        self.assertNotEqual(code, 0)
        self.assertEqual(rb(self.raw), before)

    def test_refuses_system_internal_unknown_and_readonly_devices(self):
        before = rb(self.raw)
        cases = [("system", dict(is_system=True)), ("unknown-system", dict(is_system=None)),
                 ("internal", dict(internal=True, removable=False)), ("read-only", dict(read_only=True))]
        for label, attrs in cases:
            for k, v in attrs.items():
                setattr(self.dev, k, v)
            code, _, err = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=self.CONFIRM)
            self.assertEqual(code, 1, (label, err))
            self.assertEqual(rb(self.raw), before, label)
            self.dev = make_device(size=SIZE, mounts=None)
            self.backend.devices = [self.dev]

    def test_mounted_device_is_unmounted_first_then_written(self):
        self.dev.mount_points, self.dev.mounted = ["/Volumes/CARD"], True
        self.backend.devices = [self.dev]
        order = []
        real_unmount = self.backend.unmount

        def spy_unmount(d):
            order.append(("unmount", rb(self.raw) != rb(self.src)))     # w chwili odmontowania jeszcze nie zapisano
            self.dev.mount_points, self.dev.mounted = [], False
            return real_unmount(d)
        self.backend.unmount = spy_unmount
        code, _, err = self.run_cli("image", "restore", "fake0", "--image", self.img,
                                    inputs=["/dev/fake0", "SN12345", "NADPISZ"])
        self.assertEqual(code, 0, err)
        self.assertEqual(order, [("unmount", True)])
        self.assertEqual(rb(self.raw), rb(self.src))

    def test_dry_run_needs_no_confirmation_and_writes_nothing(self):
        before = rb(self.raw)
        code, out, err = self.run_cli("dry-run", "image", "restore", "fake0", "--image", self.img)
        self.assertEqual(code, 0, err)
        self.assertIn("SYMULACJA", out)
        self.assertEqual(rb(self.raw), before)

    def test_blocked_dry_run_explains_why(self):
        self.dev.is_system = True
        code, out, err = self.run_cli("dry-run", "image", "restore", "fake0", "--image", self.img)
        self.assertIn("zablokowana", err)

    def test_problems_with_image_block_before_any_prompt(self):
        os.unlink(imaging.sidecar_path(self.img))
        code, out, err = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=self.CONFIRM)
        self.assertEqual(code, 1)
        self.assertIn(".mtinfo.json", err)
        self.assertNotIn("Wpisz pełną ścieżkę", err)         # nie męczymy pytaniami, skoro i tak się nie uda
        code, *_ = self.run_cli("image", "restore", "fake0", "--image", self.img,
                                "--allow-image-without-metadata", inputs=self.CONFIRM)
        self.assertEqual(code, 0)

    def test_failed_verification_gives_exit_code_3(self):
        bad = {"match": False, "mismatched_chunks": 2, "first_mismatch_offset": 100}
        with mock.patch.object(imaging, "verify_image", return_value=bad):
            code, out, err = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=self.CONFIRM)
        self.assertEqual(code, 3)
        self.assertIn("WERYFIKACJA NIE PRZESZŁA", out)

    def test_windows_is_refused(self):
        before = rb(self.raw)
        with mock.patch("media_tool.commands.restore_supported", return_value=False):
            code, _, err = self.run_cli("image", "restore", "fake0", "--image", self.img, inputs=self.CONFIRM)
        self.assertEqual(code, 1)
        self.assertIn("Windows", err)
        self.assertEqual(rb(self.raw), before)


if __name__ == "__main__":
    unittest.main()

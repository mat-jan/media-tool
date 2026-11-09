import json
import os
import unittest
from unittest import mock

from media_tool import backup, cli
from tests.helpers import FakeBackend, IsolatedHome, make_device, scripted_console
from tests.test_backup import rb, write


class BackupCliTests(IsolatedHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.tmp, "card")
        self.dst = os.path.join(self.tmp, "backups")
        write(os.path.join(self.src, "DCIM", "A.JPG"), b"a" * 3000, 1_700_000_000)
        write(os.path.join(self.src, "DCIM", "B.MP4"), b"b" * 3000, 1_700_000_100)
        write(os.path.join(self.src, "doc.txt"), b"t", 1_700_000_200)
        self.dev = make_device(mounts=[self.src])
        self.dev.partitions[0].volume_label = "EOS_DIGITAL"
        self.backend = FakeBackend([self.dev])
        # testy leżą na jednym woluminie – wyłączamy tylko kontrolę "ten sam wolumin" (jest testowana osobno)
        self._p = mock.patch.object(backup, "check_paths", return_value=[])
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def run_cli(self, *argv, inputs=None):
        con = scripted_console(inputs)
        with mock.patch.object(cli, "get_backend", return_value=self.backend):
            code = cli.main(list(argv), console=con)
        return code, con.captured_out.getvalue(), con.captured_err.getvalue()

    def test_backup_from_device_names_set_after_volume_label_and_never_touches_card(self):
        before = {p: rb(p) for p in (os.path.join(dp, f) for dp, _, fs in os.walk(self.src) for f in fs)}
        code, out, err = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("Wszystko skopiowane i zweryfikowane", out)
        self.assertIn("Wybrane urządzenie", err)                                   # dane urządzenia pokazane przed operacją
        self.assertEqual(rb(os.path.join(self.dst, "EOS_DIGITAL", "DCIM", "A.JPG")), b"a" * 3000)
        self.assertEqual({p: rb(p) for p in before}, before)                      # karta bez zmian
        self.assertEqual(sorted(os.listdir(self.src)), ["DCIM", "doc.txt"])       # nic nie dopisano na kartę

    def test_asks_for_confirmation_without_yes(self):
        code, _, _ = self.run_cli("backup", "run", "fake0", "-o", self.dst, inputs=["n"])
        self.assertEqual(code, 130)
        self.assertFalse(os.path.exists(self.dst))
        code, *_ = self.run_cli("backup", "run", "fake0", "-o", self.dst, inputs=["t"])
        self.assertEqual(code, 0)

    def test_json_only_and_name_and_category_filter(self):
        code, out, err = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes", "--name", "Moja karta",
                                      "--only", "photos", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual((data["name"], data["copied"]), ("Moja karta", 1))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "Moja karta", "DCIM", "A.JPG")))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "Moja karta", "doc.txt")))

    def test_unknown_category_and_unmounted_device(self):
        code, _, err = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes", "--only", "memy")
        self.assertEqual(code, 1)
        self.assertIn("Nieznana kategoria", err)
        self.dev.mount_points, self.dev.mounted = [], False
        code, _, err = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("zamontowanego woluminu", err)

    def test_several_volumes_interactive_choice_and_noninteractive_requires_flag(self):
        other = os.path.join(self.tmp, "card2")
        write(os.path.join(other, "x.jpg"), b"x")
        self.dev.mount_points = [self.src, other]
        # interaktywnie program pyta, który wolumin
        code, _, err = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes", inputs=["2"])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "TestCard-SN12345", "x.jpg")))
        # bez terminala musi być jawne --volume
        con = scripted_console([])
        con.interactive = False
        with mock.patch.object(cli, "get_backend", return_value=self.backend):
            code = cli.main(["backup", "run", "fake0", "-o", self.dst + "2", "--yes"], console=con)
        self.assertEqual(code, 1)
        self.assertIn("--volume", con.captured_err.getvalue())
        code, *_ = self.run_cli("backup", "run", "fake0", "-o", self.dst + "3", "--yes", "--volume", other)
        self.assertEqual(code, 0)

    def test_source_folder_without_device_and_dry_run(self):
        code, out, err = self.run_cli("dry-run", "backup", "run", "--source", self.src, "-o", self.dst)
        self.assertEqual(code, 0, err)
        self.assertIn("SYMULACJA", out)
        self.assertFalse(os.path.exists(self.dst))
        code, *_ = self.run_cli("backup", "run", "--source", self.src, "-o", self.dst)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "card", "doc.txt")))

    def test_failed_files_give_exit_code_3_and_second_run_completes(self):
        bad = os.path.join(self.src, "DCIM", "B.MP4")
        real_open = open

        def flaky(path, *a, **k):
            if os.fspath(path) == bad and "rb" in (a[0] if a else k.get("mode", "r")):
                raise OSError(5, "Input/output error")
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", flaky):
            code, out, _ = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("z błędami", out)
        code, *_ = self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes")
        self.assertEqual(code, 0)

    def test_verify_and_list_commands(self):
        self.run_cli("backup", "run", "fake0", "-o", self.dst, "--yes")
        s = os.path.join(self.dst, "EOS_DIGITAL")
        code, out, _ = self.run_cli("backup", "verify", s)
        self.assertEqual(code, 0)
        self.assertIn("nienaruszona", out)
        code, out, _ = self.run_cli("backup", "list", self.dst)
        self.assertIn("EOS_DIGITAL", out)
        with open(os.path.join(s, "doc.txt"), "wb") as f:
            f.write(b"X")
        code, out, _ = self.run_cli("backup", "verify", s)
        self.assertEqual(code, 3)
        self.assertIn("USZKODZONY", out)
        code, _, err = self.run_cli("backup", "verify", self.dst)
        self.assertEqual(code, 1)


class MenuBackupFlowTests(IsolatedHome, unittest.TestCase):
    def test_backup_through_menu_then_eject_prompt(self):
        from media_tool import commands, config, menu
        src = os.path.join(self.tmp, "card")
        dst = os.path.join(self.tmp, "kopie")
        write(os.path.join(src, "IMG.JPG"), b"img" * 100, 1_700_000_000)
        dev = make_device(mounts=[src])
        backend = FakeBackend([dev])
        item = 1 + sum(len(items) for _, items in menu.GROUPS[:1])               # pierwsza pozycja grupy KOPIE ZAPASOWE
        con = scripted_console([str(item),
                                "2", "1",          # tryb: same pliki -> pierwsze urządzenie
                                dst, "1", "t",     # folder, wszystkie, weryfikacja
                                "t",               # potwierdzenie operacji na nośniku
                                "n",               # nie wysuwaj
                                str(len(menu.ITEMS) + 1)])
        ctx = commands.Context(backend=backend, console=con, cfg=config.load())
        with mock.patch.object(backup, "check_paths", return_value=[]):
            self.assertEqual(menu.run(ctx), 0)
        names = os.listdir(dst)
        self.assertEqual(len(names), 1)
        self.assertEqual(rb(os.path.join(dst, names[0], "IMG.JPG")), b"img" * 100)
        self.assertEqual(backend.calls, [])                                       # "n" = karta nie została wysunięta


if __name__ == "__main__":
    unittest.main()

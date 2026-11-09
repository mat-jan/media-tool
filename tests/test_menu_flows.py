"""Przepływy menu: kopia całej karty, okna wyboru, ponowne uruchomienie z uprawnieniami administratora."""
import json
import os
import unittest
from unittest import mock

from media_tool import admin, backup, cli, commands, config, imaging, menu, picker
from media_tool.ui import UserAbort
from tests.helpers import FakeBackend, IsolatedHome, make_device, make_gpt_image, scripted_console
from tests.test_backup import rb, write

SIZE = 1024 * 1024


def item_index(key):
    return next(i for i, (k, _, _) in enumerate(menu.ITEMS, 1) if k == key)


class FullCardBackupTests(IsolatedHome, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.raw = make_gpt_image(os.path.join(self.tmp, "card.bin"), sectors=SIZE // 512)
        self.dest = os.path.join(self.tmp, "kopie")
        self.dev = make_device(size=SIZE, mounts=None, volume_label="EOS_DIGITAL")
        self.dev.partitions[0].volume_label = "EOS_DIGITAL"
        self.backend = FakeBackend([self.dev], raw=self.raw)
        self.item = item_index("backup")

    def ctx(self, inputs):
        con = scripted_console(inputs)
        return commands.Context(backend=self.backend, console=con, cfg=config.load()), con

    def run_menu(self, inputs, admin_now=True):
        ctx, con = self.ctx(inputs + [str(len(menu.ITEMS) + 1)])
        with mock.patch.object(menu, "is_admin", return_value=admin_now):
            code = menu.run(ctx)
        return code, con

    def test_whole_card_image_is_created_verified_and_restorable(self):
        code, con = self.run_menu([str(self.item), "1", "1",          # tryb: CAŁA KARTA, pierwsze urządzenie
                                   self.dest, "",                      # folder, nazwa domyślna
                                   "t",                                # zgoda na odczyt karty
                                   "t",                                # zweryfikować obraz
                                   "n"])                               # nie wysuwaj
        self.assertEqual(code, 0, con.captured_err.getvalue())
        imgs = [f for f in os.listdir(self.dest) if f.endswith(".img")]
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0].startswith("EOS_DIGITAL-SN12345-"))
        img = os.path.join(self.dest, imgs[0])
        self.assertEqual(rb(img), rb(self.raw))                        # 1:1 – CAŁE urządzenie, nie tylko wolumin
        side = imaging.load_sidecar(img)
        self.assertTrue(side["progress"]["complete"])
        out = con.captured_out.getvalue()
        self.assertIn("ZGODNY", out)                                   # weryfikacja po kopii
        # tak powstały obraz da się przywrócić (metadane kompletne)
        self.assertTrue(imaging.plan_restore(self.raw, SIZE, img)["ok"])

    def test_whole_card_copies_data_outside_any_mounted_volume(self):
        """Różnica wobec kopii plików: obraz zawiera też tablicę partycji i miejsce poza woluminem."""
        with open(self.raw, "r+b") as f:
            f.seek(SIZE - 4096)
            f.write(b"HIDDEN-PARTITION-DATA")
        self.run_menu([str(self.item), "1", "1", self.dest, "", "t", "n", "n"])
        img = os.path.join(self.dest, next(f for f in os.listdir(self.dest) if f.endswith(".img")))
        self.assertIn(b"HIDDEN-PARTITION-DATA", rb(img))
        self.assertEqual(rb(img)[:512], rb(self.raw)[:512])            # sektor 0 (MBR) też

    def test_mounted_card_offers_unmount_first(self):
        self.dev.mount_points, self.dev.mounted = ["/Volumes/EOS"], True
        self.backend.devices = [self.dev]

        def unmount(d):
            self.dev.mount_points, self.dev.mounted = [], False
            self.backend.calls.append(("unmount", d.path))
            return ["ok"]
        self.backend.unmount = unmount
        code, con = self.run_menu([str(self.item), "1", "1", self.dest, "",
                                   "t",                                  # odmontować przed kopią
                                   "/dev/fake0", "SN12345", "POTWIERDZAM",  # potwierdzenie odmontowania
                                   "t", "n", "n"])
        self.assertEqual(code, 0, con.captured_err.getvalue())
        self.assertEqual(self.backend.calls, [("unmount", "/dev/fake0")])
        self.assertTrue(any(f.endswith(".img") for f in os.listdir(self.dest)))

    def test_without_admin_offers_relaunch_and_creates_nothing(self):
        with mock.patch.object(admin, "can_elevate", return_value=True), \
             mock.patch.object(admin, "relaunch_as_admin") as relaunch:
            code, con = self.run_menu([str(self.item), "1", "1", "t"], admin_now=False)    # "t" = uruchom ponownie
        relaunch.assert_called_once_with(start_item=self.item)           # wróci dokładnie do tej funkcji
        self.assertFalse(os.path.exists(self.dest))
        self.assertIn("Potrzebne uprawnienia administratora", con.captured_out.getvalue())

    def test_declining_relaunch_returns_to_menu_without_doing_anything(self):
        with mock.patch.object(admin, "can_elevate", return_value=True), \
             mock.patch.object(admin, "relaunch_as_admin") as relaunch:
            code, con = self.run_menu([str(self.item), "1", "1", "n"], admin_now=False)
        relaunch.assert_not_called()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.dest))

    def test_not_enough_space_is_reported_before_reading_the_card(self):
        small = mock.Mock(free=1000)
        with mock.patch("shutil.disk_usage", return_value=small):
            code, con = self.run_menu([str(self.item), "1", "1", self.dest, ""])
        self.assertIn("Za mało miejsca", con.captured_err.getvalue())
        self.assertFalse(any(f.endswith(".img") for f in os.listdir(self.dest)) if os.path.isdir(self.dest) else False)

    def test_files_mode_backs_up_all_volumes(self):
        a, b = os.path.join(self.tmp, "volA"), os.path.join(self.tmp, "volB")
        write(os.path.join(a, "DCIM", "1.JPG"), b"one")
        write(os.path.join(b, "data.txt"), b"two")
        self.dev.mount_points, self.dev.mounted = [a, b], True
        self.dev.partitions[0].mount_points, self.dev.partitions[0].volume_label = [a], "BOOT"
        self.backend.devices = [self.dev]
        with mock.patch.object(backup, "check_paths", return_value=[]):
            code, con = self.run_menu([str(self.item), "2", "1", self.dest, "1", "t", "t", "n"])
        self.assertEqual(code, 0, con.captured_err.getvalue())
        sets = sorted(d for d in os.listdir(self.dest))
        self.assertEqual(len(sets), 2, sets)
        files = sorted(f for s in sets for dp, _, fs in os.walk(os.path.join(self.dest, s)) for f in fs
                       if not dp.endswith(("tmp", "runs")) and f not in ("index.json",) and not f.endswith(".json"))
        self.assertEqual(files, ["1.JPG", "data.txt"])

    def test_start_item_env_jumps_straight_to_the_function_after_elevation(self):
        ctx, con = self.ctx(["1", "1", self.dest, "", "t", "n", "n", str(len(menu.ITEMS) + 1)])
        with mock.patch.object(menu, "is_admin", return_value=True), \
             mock.patch.dict(os.environ, {admin.START_ITEM_ENV: str(self.item)}):
            code = menu.run(ctx)
        self.assertEqual(code, 0)
        self.assertNotIn(admin.START_ITEM_ENV, os.environ)
        self.assertTrue(any(f.endswith(".img") for f in os.listdir(self.dest)))


class NamingTests(IsolatedHome, unittest.TestCase):
    def test_image_name_uses_single_label_else_model_and_never_glues_labels(self):
        d = make_device(volume_label="KARTA1, KARTA2")
        d.partitions[0].volume_label = "EOS_DIGITAL"
        self.assertTrue(menu._card_image_name(d).startswith("EOS_DIGITAL-SN12345-"))
        from media_tool.models import Partition
        d.partitions.append(Partition(identifier="p2", volume_label="DRUGA"))
        name = menu._card_image_name(d)
        self.assertTrue(name.startswith("TestCard-SN12345-"), name)
        self.assertTrue(name.endswith(".img"))


class CliVolumeAllTests(IsolatedHome, unittest.TestCase):
    def test_volume_all_creates_one_set_per_volume_and_aggregates_json(self):
        a, b = os.path.join(self.tmp, "volA"), os.path.join(self.tmp, "volB")
        write(os.path.join(a, "x.jpg"), b"x" * 50)
        write(os.path.join(b, "y.jpg"), b"y" * 50)
        dev = make_device(mounts=[a, b])
        dev.partitions[0].mount_points, dev.partitions[0].volume_label = [a], "ALFA"
        dest = os.path.join(self.tmp, "out")
        con = scripted_console([])
        with mock.patch.object(cli, "get_backend", return_value=FakeBackend([dev])), \
             mock.patch.object(backup, "check_paths", return_value=[]):
            code = cli.main(["backup", "run", "fake0", "-o", dest, "--yes", "--volume", "all", "--json"], console=con)
        self.assertEqual(code, 0, con.captured_err.getvalue())
        data = json.loads(con.captured_out.getvalue())
        self.assertEqual(len(data["sets"]), 2)
        self.assertEqual(data["copied"], 2)
        self.assertTrue(os.path.isfile(os.path.join(dest, "TestCard-SN12345-ALFA", "x.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "TestCard-SN12345-volB", "y.jpg")))


class FolderPickerFlowTests(IsolatedHome, unittest.TestCase):
    def ctx(self, inputs):
        con = scripted_console(inputs)
        return commands.Context(backend=FakeBackend([]), console=con, cfg=config.load()), con

    def test_window_choice_returns_picked_folder_without_typing(self):
        ctx, con = self.ctx(["1"])                                   # 1 = wybierz w oknie
        with mock.patch.object(picker, "gui_available", return_value=True), \
             mock.patch.object(picker, "pick_folder", return_value="/Users/me/Kopie") as pf:
            self.assertEqual(menu.ask_folder(ctx, "Gdzie zapisać", "/Users/me/media-tool/backups"), "/Users/me/Kopie")
        pf.assert_called_once()
        self.assertEqual(pf.call_args.args[1], "/Users/me/media-tool/backups")      # okno startuje w domyślnym folderze

    def test_default_and_typed_choices(self):
        ctx, _ = self.ctx(["2"])
        with mock.patch.object(picker, "gui_available", return_value=True):
            self.assertEqual(menu.ask_folder(ctx, "x", "/dom/domyslny"), "/dom/domyslny")
        ctx, _ = self.ctx(["3", "/wpisany/folder"])
        with mock.patch.object(picker, "gui_available", return_value=True):
            self.assertEqual(menu.ask_folder(ctx, "x", "/dom/domyslny"), "/wpisany/folder")

    def test_cancelling_the_window_aborts_instead_of_silently_using_default(self):
        ctx, _ = self.ctx(["1"])
        with mock.patch.object(picker, "gui_available", return_value=True), \
             mock.patch.object(picker, "pick_folder", return_value=None):
            with self.assertRaises(UserAbort):
                menu.ask_folder(ctx, "x", "/dom")

    def test_broken_window_falls_back_to_typing(self):
        ctx, con = self.ctx(["1", "/reczna/sciezka"])
        with mock.patch.object(picker, "gui_available", return_value=True), \
             mock.patch.object(picker, "pick_folder", side_effect=picker.PickerUnavailable("boom")):
            self.assertEqual(menu.ask_folder(ctx, "x", ""), "/reczna/sciezka")
        self.assertIn("niedostępne", con.captured_err.getvalue())

    def test_without_gui_it_just_asks_for_a_path_with_default(self):
        ctx, _ = self.ctx([""])                                      # Enter = domyślna
        self.assertEqual(menu.ask_folder(ctx, "x", "/dom/domyslny"), "/dom/domyslny")

    def test_file_picker_and_output_file_name(self):
        ctx, _ = self.ctx(["1"])
        with mock.patch.object(picker, "gui_available", return_value=True), \
             mock.patch.object(picker, "pick_file", return_value="/tmp/b.img"):
            self.assertEqual(menu.ask_file(ctx, "Plik obrazu"), "/tmp/b.img")
        ctx, _ = self.ctx(["2", "moj.img"])                           # domyślny folder + własna nazwa
        with mock.patch.object(picker, "gui_available", return_value=True):
            self.assertEqual(menu.ask_output_file(ctx, "Gdzie", "domyslna.img", "/kopie"),
                             os.path.join("/kopie", "moj.img"))


if __name__ == "__main__":
    unittest.main()

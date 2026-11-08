import json
import os
import unittest
from unittest import mock

from media_tool import cli, imaging
from tests.helpers import FakeBackend, IsolatedHome, make_device, make_file, make_gpt_image, scripted_console

SIZE = 2 * 1024 * 1024


class CliBase(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.raw = make_gpt_image(os.path.join(self.tmp, "raw.bin"), sectors=SIZE // 512)
        self.dev = make_device(size=SIZE, mounts=None)
        self.backend = FakeBackend([self.dev], raw=self.raw)
        self.outdir = os.path.join(self.tmp, "o")
        os.makedirs(self.outdir)

    def run_cli(self, *argv, inputs=None, yes=False):
        con = scripted_console(inputs)
        with mock.patch.object(cli, "get_backend", return_value=self.backend):
            code = cli.main(list(argv), console=con)
        return code, con.captured_out.getvalue(), con.captured_err.getvalue()

    def js(self, *argv, **kw):
        code, out, err = self.run_cli(*argv, "--json", **kw)
        return code, (json.loads(out) if out.strip() else None), err


class InfoCommandTests(CliBase, unittest.TestCase):
    def test_list_text_and_json(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("/dev/fake0", out)
        code, data, _ = self.js("list")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["devices"][0]["wwn"], "NIEDOSTĘPNE")

    def test_flags_work_before_and_after_subcommand(self):
        a = self.run_cli("--json", "serial", "fake0")
        b = self.run_cli("serial", "fake0", "--json")
        self.assertEqual(json.loads(a[1]), json.loads(b[1]))

    def test_serial_distinguishes_kinds_and_warns(self):
        code, out, _ = self.run_cli("serial", "/dev/fake0")
        self.assertIn("Numer seryjny urządzenia", out)
        self.assertIn("karty SD", out)
        self.assertIn("czytnika/mostka", out)
        self.assertIn("NIEDOSTĘPNE", out)
        self.assertIn("dotyczyć czytnika", out)

    def test_inspect_ids_uuids_partitions_mount_health_space(self):
        for cmd, needle in (("inspect", "Stan zdrowia"), ("ids", "WWN"), ("uuids", "ABCD-1234"),
                            ("partitions", "fake0s1"), ("mount-status", "Zamontowany"), ("health", "PASSED"),
                            ("system-status", "Dysk systemowy"), ("space", "Brak zamontowanych")):
            code, out, err = self.run_cli(cmd, "fake0")
            self.assertEqual(code, 0, (cmd, err))
            self.assertIn(needle, out, cmd)

    def test_selecting_by_serial(self):
        code, data, _ = self.js("mount-status", "serial:SN12345")
        self.assertEqual((code, data["path"]), (0, "/dev/fake0"))
        code, _, err = self.run_cli("mount-status", "serial:NOPE")
        self.assertEqual(code, 1)
        self.assertIn("Nie znaleziono", err)


class MediaReadTests(CliBase, unittest.TestCase):
    def test_read_ops_need_confirmation(self):
        code, out, err = self.run_cli("scan", "fake0", inputs=["n"])
        self.assertEqual(code, 130)
        code, out, err = self.run_cli("scan", "fake0", inputs=["t"])
        self.assertEqual(code, 0, err)
        self.assertIn("CZYSTO", out)
        self.assertIn("Wybrane urządzenie", err)           # dane urządzenia pokazane przed operacją
        code, *_ = self.run_cli("scan", "fake0", "--yes")
        self.assertEqual(code, 0)

    def test_scan_integrity_benchmark_errors(self):
        code, data, err = self.js("scan", "fake0", "--yes")
        self.assertEqual((code, data["clean"], data["bytes_read"]), (0, True, SIZE), err)
        code, data, err = self.js("integrity", "fake0", "--yes", "--seed", "1")
        self.assertEqual(code, 0, err)
        self.assertTrue(data["passed"])
        self.assertEqual(data["partition_table"]["scheme"], "GPT")
        code, data, err = self.js("integrity", "fake0", "--yes", "--range", "0:4096", "--range", "4096:4096")
        self.assertEqual(data["areas_checked"], 2)
        code, data, err = self.js("benchmark", "fake0", "--yes", "--size-mib", "1")
        self.assertEqual(code, 0, err)
        self.assertGreater(data["seq_avg_mb_s"], 0)
        self.assertEqual(data["read_errors"], 0)
        code, data, err = self.js("errors", "fake0", "--yes")
        self.assertEqual((code, data["smart_error_count"]), (0, 0))

    def test_scan_reports_problem_exit_code(self):
        with open(self.raw, "r+b") as f:
            f.truncate(SIZE // 2)         # urządzenie "kończy się" wcześniej niż deklaruje
        code, data, _ = self.js("scan", "fake0", "--yes")
        self.assertEqual(code, 3)
        self.assertFalse(data["clean"])

    def test_dry_run_reads_and_writes_nothing(self):
        out_img = os.path.join(self.outdir, "x.img")
        code, out, err = self.run_cli("dry-run", "image", "create", "fake0", "-o", out_img, "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("SYMULACJA", out)
        self.assertFalse(os.path.exists(out_img))
        self.assertFalse(os.path.exists(imaging.sidecar_path(out_img)))
        code, out, _ = self.run_cli("--dry-run", "scan", "fake0", "--yes")
        self.assertIn("SYMULACJA", out)


class ImageCommandTests(CliBase, unittest.TestCase):
    def test_create_verify_inspect_resume_flow(self):
        img = os.path.join(self.outdir, "b.img")
        code, data, err = self.js("image", "create", "fake0", "-o", img, "--yes")
        self.assertEqual(code, 0, err)
        with open(self.raw, "rb") as f:
            import hashlib
            self.assertEqual(data["image"]["hash"], hashlib.sha256(f.read()).hexdigest())
        code, data, err = self.js("image", "verify", "fake0", "--image", img, "--yes")
        self.assertEqual((code, data["match"]), (0, True), err)
        code, data, err = self.js("image", "inspect", img, "--hash")
        self.assertEqual(code, 0, err)
        self.assertEqual(data["partition_table"]["scheme"], "GPT")
        self.assertTrue(data["hash_matches_record"])
        # modyfikacja nośnika => verify zwraca kod 3
        with open(self.raw, "r+b") as f:
            f.seek(1000000)
            f.write(b"CHANGED")
        code, data, _ = self.js("image", "verify", "fake0", "--image", img, "--yes")
        self.assertEqual((code, data["match"]), (3, False))
        # drugi raz create bez --overwrite => błąd
        code, _, err = self.run_cli("image", "create", "fake0", "-o", img, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("już istnieje", err)
        # resume kompletnego obrazu => błąd
        code, _, err = self.run_cli("image", "resume", "fake0", "-o", img, "--yes")
        self.assertEqual(code, 1)

    def test_image_on_source_volume_is_refused(self):
        self.dev.mount_points = [self.outdir]
        self.dev.mounted = True
        img = os.path.join(self.outdir, "b.img")
        code, _, err = self.run_cli("image", "create", "fake0", "-o", img, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("nośniku", err)
        self.assertFalse(os.path.exists(img))

    def test_hash_and_compare(self):
        a = make_file(os.path.join(self.outdir, "a"), 100000)
        b = os.path.join(self.outdir, "b")
        with open(a, "rb") as f, open(b, "wb") as g:
            g.write(f.read())
        code, data, _ = self.js("hash", a)
        self.assertEqual(len(data["hash"]), 64)
        code, data, _ = self.js("compare", a, b)
        self.assertEqual((code, data["identical"]), (0, True))
        with open(b, "ab") as g:
            g.write(b"x")
        code, data, _ = self.js("compare", a, b)
        self.assertEqual((code, data["identical"]), (3, False))


class ReportCommandTests(CliBase, unittest.TestCase):
    def test_reports_all_formats_and_diff(self):
        paths = {}
        for fmt in ("json", "csv", "html", "txt"):
            p = os.path.join(self.outdir, f"r.{fmt}")
            code, _, err = self.run_cli("report", "--format", fmt, "-o", p)
            self.assertEqual(code, 0, err)
            paths[fmt] = p
            self.assertGreater(os.path.getsize(p), 100)
        code, _, err = self.run_cli("report", "--format", "json", "-o", paths["json"])
        self.assertEqual(code, 1)                     # bez --overwrite nie nadpisuje
        code, _, _ = self.run_cli("report", "--format", "json", "-o", paths["json"], "--overwrite", "--redact")
        with open(paths["json"], encoding="utf-8") as f:
            self.assertNotIn("SN12345", f.read())
        self.dev.mount_points = ["/Volumes/NEW"]
        p2 = os.path.join(self.outdir, "r2.json")
        self.run_cli("report", "--format", "json", "-o", p2, "--no-redact")
        p1 = os.path.join(self.outdir, "r1.json")
        self.dev.mount_points = []
        self.run_cli("report", "--format", "json", "-o", p1, "--no-redact")
        code, data, _ = self.js("report-diff", p1, p2)
        self.assertEqual(code, 3)
        self.assertTrue(any(c["field"] == "mount_points" for c in data["changes"]))

    def test_dry_run_report_writes_nothing(self):
        p = os.path.join(self.outdir, "no.json")
        self.run_cli("dry-run", "report", "-o", p)
        self.assertFalse(os.path.exists(p))


class StateChangeTests(CliBase, unittest.TestCase):
    def test_unmount_requires_typed_confirmation(self):
        code, out, err = self.run_cli("unmount", "fake0", inputs=["/dev/fake0", "SN12345", "POTWIERDZAM"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.backend.calls, [("unmount", "/dev/fake0")])

    def test_unmount_refused_on_any_wrong_answer_and_no_call_made(self):
        for inputs in (["/dev/fake0", "SN12345", "tak"], ["x", "SN12345", "POTWIERDZAM"], []):
            code, _, _ = self.run_cli("unmount", "fake0", inputs=inputs)
            self.assertNotEqual(code, 0)
        self.assertEqual(self.backend.calls, [])

    def test_yes_does_not_unlock_state_changes_and_system_disk_is_blocked(self):
        code, *_ = self.run_cli("unmount", "fake0", "--yes")
        self.assertNotEqual(code, 0)
        self.dev.is_system = True
        code, _, err = self.run_cli("eject", "fake0", inputs=["/dev/fake0", "SN12345", "POTWIERDZAM"])
        self.assertEqual(code, 1)
        self.assertIn("systemowy", err)
        self.assertEqual(self.backend.calls, [])

    def test_device_change_after_confirmation_aborts(self):
        real_list = self.backend.list_devices
        state = {"n": 0}

        def flaky(include_virtual=False):
            state["n"] += 1
            devs = real_list()
            if state["n"] >= 2:                      # 1. wywołanie: wybór; 2.: ponowna weryfikacja
                devs = [make_device(size=SIZE, serial="DIFFERENT")]
            return devs
        self.backend.list_devices = flaky
        code, _, err = self.run_cli("unmount", "fake0", inputs=["/dev/fake0", "SN12345", "POTWIERDZAM"])
        self.assertEqual(code, 1)
        self.assertIn("zmieniły się", err)
        self.assertEqual(self.backend.calls, [])

    def test_dry_run_unmount_does_nothing(self):
        code, out, _ = self.run_cli("dry-run", "unmount", "fake0", inputs=["/dev/fake0", "SN12345", "POTWIERDZAM"])
        self.assertEqual(code, 0)
        self.assertIn("SYMULACJA", out)
        self.assertEqual(self.backend.calls, [])


class MiscCommandTests(CliBase, unittest.TestCase):
    def test_config_logs_features_diagnose(self):
        self.assertEqual(self.run_cli("config", "set", "chunk_size_mib", "8")[0], 0)
        self.assertEqual(self.run_cli("config", "get", "chunk_size_mib")[1].strip(), "8")
        self.assertEqual(self.run_cli("config", "set", "chunk_size_mib", "0")[0], 1)
        self.assertEqual(self.run_cli("config", "reset")[0], 0)
        code, out, _ = self.run_cli("logs")
        self.assertEqual(code, 0)
        self.assertIn("AUDIT", out)
        code, data, _ = self.js("features")
        self.assertIn("image_create", data)
        code, data, _ = self.js("diagnose")
        self.assertEqual(code, 0)
        self.assertTrue(data["device_listing"]["ok"])

    def test_no_command_non_interactive_prints_help(self):
        import contextlib
        import io
        con = scripted_console([])
        con.interactive = False
        buf = io.StringIO()
        with mock.patch.object(cli, "get_backend", return_value=self.backend), contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main([], console=con), 0)
        self.assertIn("POLECENIE", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

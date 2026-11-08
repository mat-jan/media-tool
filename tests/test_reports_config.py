import csv
import io
import json
import os
import unittest

from media_tool import config, logs, reports
from media_tool.models import UNAVAILABLE, Health, device_from_dict
from tests.helpers import IsolatedHome, make_device


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.d = make_device(serial="SN12345", mounts=["/Volumes/CARD"])
        self.d.health = Health(status="PASSED", temperature_c=31, power_on_hours=100, error_count=0, source="smartctl")
        self.snap = reports.build_snapshot([self.d], "test")

    def test_missing_values_are_marked_not_empty(self):
        dev = self.snap["devices"][0]
        self.assertEqual(dev["wwn"], UNAVAILABLE)
        self.assertEqual(dev["controller_serial"], UNAVAILABLE)
        self.assertEqual(dev["partitions"][0]["offset_bytes"], UNAVAILABLE)
        self.assertEqual(dev["serial"], "SN12345")

    def test_all_formats_render_and_contain_key_data(self):
        for fmt in reports.FORMATS:
            out = reports.render(self.snap, fmt)
            self.assertIn("SN12345", out, fmt)
            self.assertIn("/dev/fake0", out, fmt)
        self.assertEqual(json.loads(reports.render(self.snap, "json"))["devices"][0]["model"], "TestCard")

    def test_csv_has_device_and_partition_rows_and_is_parseable(self):
        rows = list(csv.DictReader(io.StringIO(reports.render(self.snap, "csv"))))
        self.assertEqual([r["record_type"] for r in rows], ["device", "partition"])
        self.assertEqual(rows[0]["serial"], "SN12345")
        self.assertEqual(rows[1]["part_fs_uuid"], "ABCD-1234")

    def test_csv_formula_injection_is_neutralised(self):
        self.d.volume_label = "=HYPERLINK(\"http://evil\")"
        out = reports.render(reports.build_snapshot([self.d], "t"), "csv")
        row = next(csv.DictReader(io.StringIO(out)))
        self.assertTrue(row["volume_label"].startswith("'="))

    def test_html_escapes_untrusted_values(self):
        self.d.model = "<script>alert(1)</script>"
        out = reports.render(reports.build_snapshot([self.d], "t"), "html")
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_redaction_masks_serials_only(self):
        snap = reports.build_snapshot([self.d], "t", redact=True)
        dev = snap["devices"][0]
        self.assertNotEqual(dev["serial"], "SN12345")
        self.assertTrue(dev["serial"].startswith("SN") and "*" in dev["serial"])
        self.assertEqual(dev["model"], "TestCard")
        self.assertEqual(dev["wwn"], UNAVAILABLE)
        self.assertNotIn("SN12345", reports.render(snap, "json"))

    def test_roundtrip_through_json_restores_none(self):
        dev = device_from_dict(json.loads(reports.render(self.snap, "json"))["devices"][0])
        self.assertIsNone(dev.wwn)
        self.assertEqual(dev.serial, "SN12345")
        self.assertEqual(dev.partitions[0].fs_uuid, "ABCD-1234")


class DiffTests(unittest.TestCase):
    def snap(self, **kw):
        d = make_device(serial="SN1", **kw)
        d.health = Health(status=kw.get("hs", "PASSED"), temperature_c=kw.get("temp", 30), power_on_hours=kw.get("poh", 10))
        return reports.build_snapshot([d], "t")

    def test_identical_ignores_volatile_fields(self):
        a, b = self.snap(temp=30), self.snap(temp=55)
        b["devices"][0]["collected_at"] = "other"
        self.assertTrue(reports.diff_reports(a, b)["identical"])

    def test_changes_added_and_removed(self):
        a = self.snap()
        b = self.snap(poh=50)
        b["devices"][0]["health"]["status"] = "FAILED"
        b["devices"][0]["partitions"][0]["volume_label"] = "NEW"
        r = reports.diff_reports(a, b)
        fields = {c["field"]: (c["old"], c["new"]) for c in r["changes"]}
        self.assertEqual(fields["health.status"], ("PASSED", "FAILED"))
        self.assertEqual(fields["health.power_on_hours"], (10, 50))
        self.assertEqual(fields["partition[fake0s1].volume_label"], ("CARD", "NEW"))
        other = reports.build_snapshot([make_device("/dev/fake9", serial="ZZZ")], "t")
        r = reports.diff_reports(a, other)
        self.assertEqual((r["added"], r["removed"]), (["/dev/fake9"], ["/dev/fake0"]))
        self.assertFalse(r["identical"])


class ConfigLogTests(IsolatedHome, unittest.TestCase):
    def test_defaults_set_get_reset(self):
        self.assertEqual(config.load()["chunk_size_mib"], 4)
        self.assertEqual(config.set_value("chunk_size_mib", "16"), 16)
        self.assertEqual(config.load()["chunk_size_mib"], 16)
        self.assertIs(config.set_value("redact_serials", "tak"), True)
        config.reset()
        self.assertEqual(config.load(), config.defaults())

    def test_validation(self):
        for key, val in (("chunk_size_mib", "0"), ("chunk_size_mib", "abc"), ("log_level", "LOUD"),
                         ("redact_serials", "może"), ("nie_ma", "1"), ("hash_algorithm", "crc")):
            with self.assertRaises(config.ConfigError, msg=(key, val)):
                config.coerce(key, val)

    def test_corrupt_config_file_falls_back_to_defaults(self):
        os.makedirs(config.config_dir(), exist_ok=True)
        with open(config.config_path(), "w") as f:
            f.write("{nie json")
        self.assertEqual(config.load(), config.defaults())
        with open(config.config_path(), "w") as f:
            json.dump({"chunk_size_mib": 9999, "log_level": "DEBUG"}, f)
        cfg = config.load()
        self.assertEqual((cfg["chunk_size_mib"], cfg["log_level"]), (4, "DEBUG"))

    def test_logs_are_written_and_tailed(self):
        logs.setup("INFO")
        logs.audit("test_op", device="/dev/x")
        for h in logs.get().handlers:
            h.flush()
        tail = logs.tail(5)
        self.assertTrue(any("test_op" in ln and "/dev/x" in ln for ln in tail), tail)


if __name__ == "__main__":
    unittest.main()

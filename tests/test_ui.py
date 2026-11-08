import io
import os
import unittest
from unittest import mock

from media_tool import ui
from media_tool.ui import Console, format_table, strip_ansi, visible_len


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class StyleTests(unittest.TestCase):
    def tearDown(self):
        ui.set_color(False)

    def test_no_color_when_not_a_tty_or_no_color_env(self):
        self.assertFalse(ui.detect_color(io.StringIO()))
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(ui.detect_color(FakeTTY()))
        with mock.patch.dict(os.environ, {"TERM": "xterm"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(ui.detect_color(FakeTTY()) or os.name == "nt")
        with mock.patch.dict(os.environ, {"MEDIA_TOOL_COLOR": "1"}):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(ui.detect_color(io.StringIO()))

    def test_paint_is_plain_when_disabled_and_strippable_when_enabled(self):
        ui.set_color(False)
        self.assertEqual(ui.red("x"), "x")
        ui.set_color(True)
        self.assertIn("\x1b[", ui.red("x"))
        self.assertEqual(strip_ansi(ui.good("ok")), "✔ ok")
        self.assertEqual(visible_len(ui.bold(ui.cyan("abc"))), 3)

    def test_ascii_mode_has_no_unicode_symbols(self):
        with mock.patch.dict(os.environ, {"MEDIA_TOOL_ASCII": "1"}):
            self.assertEqual(ui.sym("ok"), "+")
            buf = io.StringIO()
            c = Console(out=buf, err=buf)
            c.box("T", ["abc"], stream="out")
            c.banner("x")
            self.assertTrue(all(ord(ch) < 128 for ch in buf.getvalue()))

    def test_table_aligns_by_visible_width_even_with_colored_cells(self):
        ui.set_color(True)
        rows = [["a", ui.red("SYSTEM")], [ui.green("bb"), "x"]]
        lines = [strip_ansi(l) for l in format_table(["NAZWA", "STATUS"], rows)]
        self.assertEqual([l.index("S") if "SYSTEM" in l else None for l in lines][0:1], [None])
        col = lines[0].index("STATUS")
        self.assertEqual(lines[2].index("SYSTEM"), col)
        self.assertEqual(lines[3].index("x"), col)

    def test_box_lines_have_equal_visible_width(self):
        ui.set_color(True)
        buf = io.StringIO()
        c = Console(out=buf, err=buf)
        ui.set_color(True)
        c.box("Tytuł", ["krótko", ui.red("na czerwono"), "średnio długa linia tekstu"], kind="warn", stream="out")
        widths = {visible_len(l) for l in buf.getvalue().splitlines()}
        self.assertEqual(len(widths), 1, widths)

    def test_progress_only_on_tty_and_shows_eta(self):
        c = Console(out=io.StringIO(), err=io.StringIO())
        c.progress("x", 0.5, "1 MB/s")
        self.assertEqual(c.err.getvalue(), "")                           # brak terminala = brak paska
        tty = FakeTTY()
        c = Console(out=io.StringIO(), err=tty)
        ui.set_color(False)
        times = iter([10.0, 12.0, 20.0])
        with mock.patch("time.perf_counter", lambda: next(times)):
            c.progress("kopia", 0.10, "")
            c.progress("kopia", 0.50, "5 MB/s")
        text = tty.getvalue()
        self.assertIn("50.0%", text)
        self.assertIn("ETA", text)
        c.progress("kopia", 1.0, "")
        self.assertTrue(tty.getvalue().endswith("\n"))

    def test_duration_format(self):
        self.assertEqual(ui.fmt_duration(75), "01:15")
        self.assertEqual(ui.fmt_duration(3725), "1:02:05")
        self.assertEqual(ui.fmt_duration(-5), "00:00")


if __name__ == "__main__":
    unittest.main()


class WrapTests(unittest.TestCase):
    def test_long_warning_is_wrapped_not_truncated(self):
        buf = io.StringIO()
        c = Console(out=buf, err=buf)
        msg = "WSZYSTKIE DANE na nośniku SanDisk Ultra (64 GB, /dev/disk4) zostaną NIEODWRACALNIE nadpisane obrazem. " * 2
        with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((60, 24))):
            c.box("UWAGA", [msg], kind="bad", stream="out")
        text = buf.getvalue()
        self.assertNotIn("…", text)
        flat = " ".join(l.strip("│ ").strip() for l in text.splitlines()[1:-1])
        self.assertIn("NIEODWRACALNIE nadpisane obrazem.", flat)
        self.assertTrue(all(visible_len(l) <= 60 for l in text.splitlines()))

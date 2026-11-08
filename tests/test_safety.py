import unittest

from media_tool import safety
from media_tool.errors import DeviceChanged, DeviceNotFound, SafetyError
from media_tool.safety import Risk
from media_tool.ui import UserAbort
from tests.helpers import FakeBackend, IsolatedHome, make_device, scripted_console


class ConfirmTypedTests(IsolatedHome, unittest.TestCase):
    def test_success_requires_path_serial_and_word(self):
        d = make_device()
        c = scripted_console(["/dev/fake0", "SN12345", "POTWIERDZAM"])
        safety.confirm_typed(c, d, "odmontowanie")   # nie rzuca

    def test_wrong_path_serial_or_word_aborts(self):
        d = make_device()
        for inputs in (["/dev/other", "SN12345", "POTWIERDZAM"], ["/dev/fake0", "WRONG", "POTWIERDZAM"],
                       ["/dev/fake0", "SN12345", "potwierdzam"], ["/dev/fake0", "SN12345", "tak"]):
            with self.assertRaises(SafetyError, msg=inputs):
                safety.confirm_typed(scripted_console(inputs), d, "x")

    def test_serial_prompt_skipped_when_unavailable(self):
        d = make_device(serial=None)
        c = scripted_console(["/dev/fake0", "POTWIERDZAM"])
        safety.confirm_typed(c, d, "x")
        self.assertIn("NIEDOSTĘPNY", c.captured_err.getvalue())

    def test_system_disk_is_always_blocked(self):
        d = make_device(system=True)
        with self.assertRaises(SafetyError) as cm:
            safety.confirm_typed(scripted_console(["/dev/fake0", "SN12345", "POTWIERDZAM"]), d, "x")
        self.assertIn("systemowy", str(cm.exception))

    def test_yes_flag_does_not_bypass_typed_confirmation(self):
        c = scripted_console([], assume_yes=True)
        with self.assertRaises(UserAbort):      # zapytał o ścieżkę mimo --yes; brak odpowiedzi = przerwanie
            safety.confirm_typed(c, make_device(), "x")
        self.assertIn("pełną ścieżkę", c.captured_err.getvalue())

    def test_non_interactive_is_refused(self):
        c = scripted_console([])
        c.interactive = False
        with self.assertRaises(SafetyError):
            safety.confirm_typed(c, make_device(), "x")

    def test_eof_aborts(self):
        with self.assertRaises(UserAbort):
            safety.confirm_typed(scripted_console([]), make_device(), "x")


class ConfirmReadTests(IsolatedHome, unittest.TestCase):
    def test_yes_no_answers(self):
        safety.confirm_read(scripted_console(["t"]), make_device(), "test")
        with self.assertRaises(UserAbort):
            safety.confirm_read(scripted_console(["n"]), make_device(), "test")
        with self.assertRaises(UserAbort):
            safety.confirm_read(scripted_console([""]), make_device(), "test")   # domyślnie: nie

    def test_assume_yes_skips_prompt_but_still_shows_device(self):
        c = scripted_console([], assume_yes=True)
        safety.confirm_read(c, make_device(), "test")
        self.assertIn("/dev/fake0", c.captured_err.getvalue())
        self.assertIn("SN12345", c.captured_err.getvalue())

    def test_non_interactive_without_yes_is_refused(self):
        c = scripted_console([])
        c.interactive = False
        with self.assertRaises(SafetyError):
            safety.confirm_read(c, make_device(), "test")


class RevalidateTests(IsolatedHome, unittest.TestCase):
    def test_unchanged_device_passes(self):
        d = make_device()
        self.assertEqual(safety.revalidate(FakeBackend([make_device()]), d).path, d.path)

    def test_changed_serial_size_or_mounts_is_detected(self):
        d = make_device()
        for changed in (make_device(serial="OTHER"), make_device(size=2097152)):
            with self.assertRaises(DeviceChanged):
                safety.revalidate(FakeBackend([changed]), d)
        with self.assertRaises(DeviceChanged):
            safety.revalidate(FakeBackend([make_device(mounts=["/Volumes/X"])]), d, strict=True)
        # operacje tylko do odczytu tolerują zmianę zamontowania
        safety.revalidate(FakeBackend([make_device(mounts=["/Volumes/X"])]), d, strict=False)

    def test_vanished_device(self):
        with self.assertRaises(DeviceChanged):
            safety.revalidate(FakeBackend([]), make_device())


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.a = make_device("/dev/disk4", serial="AAA")
        self.b = make_device("/dev/disk5", serial="BBB")

    def test_by_path_name_serial_and_partition(self):
        devs = [self.a, self.b]
        self.assertIs(safety.resolve_device(devs, "/dev/disk4"), self.a)
        self.assertIs(safety.resolve_device(devs, "disk5"), self.b)
        self.assertIs(safety.resolve_device(devs, "serial:bbb"), self.b)
        self.assertIs(safety.resolve_device(devs, "disk4s1"), self.a)   # partycja -> cały dysk

    def test_unknown_and_ambiguous(self):
        with self.assertRaises(DeviceNotFound):
            safety.resolve_device([self.a], "disk9")
        dup = make_device("/dev/disk6", serial="AAA")
        with self.assertRaises(SafetyError):
            safety.resolve_device([self.a, dup], "serial:AAA")

    def test_windows_style_names(self):
        w = make_device("\\\\.\\PHYSICALDRIVE1", serial="W1")
        w.system_name = "PhysicalDrive1"
        self.assertIs(safety.resolve_device([w], "PhysicalDrive1"), w)
        self.assertIs(safety.resolve_device([w], "\\\\.\\PHYSICALDRIVE1"), w)


if __name__ == "__main__":
    unittest.main()

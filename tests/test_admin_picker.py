import os
import subprocess
import sys
import unittest
from unittest import mock

from media_tool import admin, picker
from media_tool.util import CmdResult


class AdminTests(unittest.TestCase):
    def test_command_for_python_dash_m(self):
        fake_main = mock.Mock(__spec__=mock.Mock())
        fake_main.__spec__.name = "media_tool.__main__"
        with mock.patch.dict(sys.modules, {"__main__": fake_main}), mock.patch.object(sys, "argv", ["x", "list"]), \
             mock.patch.object(sys, "frozen", False, create=True):
            cmd = admin.program_command()
        self.assertEqual(cmd, [sys.executable, "-m", "media_tool", "list"])

    def test_command_for_pyz_and_frozen_binary(self):
        fake_main = mock.Mock(__spec__=None)
        with mock.patch.dict(sys.modules, {"__main__": fake_main}), \
             mock.patch.object(sys, "argv", ["/app/media-tool.pyz"]), mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(admin.program_command(), [sys.executable, "/app/media-tool.pyz"])
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "argv", ["/app/media-tool", "menu"]), \
             mock.patch.object(sys, "executable", "/app/media-tool"):
            self.assertEqual(admin.program_command(), ["/app/media-tool", "menu"])

    def test_sudo_argv_passes_environment_and_start_item(self):
        with mock.patch.object(admin, "program_command", return_value=["py", "-m", "media_tool"]), \
             mock.patch.dict(os.environ, {"PYTHONPATH": "/src", "MEDIA_TOOL_HOME": "/h"}):
            argv = admin.elevation_argv(start_item=12)
        self.assertEqual(argv[:2], ["sudo", "env"])
        self.assertIn("MEDIA_TOOL_START_ITEM=12", argv)
        self.assertIn("PYTHONPATH=/src", argv)
        self.assertEqual(argv[-3:], ["py", "-m", "media_tool"])
        self.assertTrue(any(a.startswith("HOME=") for a in argv))

    @unittest.skipIf(sys.platform == "win32", "execvp / sudo")
    def test_relaunch_execs_sudo_and_never_runs_anything_else(self):
        with mock.patch.object(admin, "program_command", return_value=["py", "-m", "media_tool"]), \
             mock.patch("os.execvp") as ex:
            admin.relaunch_as_admin(7)
        self.assertEqual(ex.call_args.args[0], "sudo")
        self.assertIn("MEDIA_TOOL_START_ITEM=7", ex.call_args.args[1])

    def test_cannot_elevate_when_already_admin(self):
        with mock.patch.object(admin, "is_admin", return_value=True):
            self.assertFalse(admin.can_elevate())


class PickerTests(unittest.TestCase):
    def test_gui_availability_rules(self):
        with mock.patch.dict(os.environ, {"MEDIA_TOOL_NO_GUI": "1"}):
            self.assertFalse(picker.gui_available())
        env = {k: v for k, v in os.environ.items() if k not in ("MEDIA_TOOL_NO_GUI", "SSH_CONNECTION")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "platform", "darwin"), \
             mock.patch.object(picker, "which", return_value="/usr/bin/osascript"):
            self.assertTrue(picker.gui_available())
            with mock.patch.dict(os.environ, {"SSH_CONNECTION": "1.2.3.4"}):
                self.assertFalse(picker.gui_available())          # zdalna sesja: bez okien
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "platform", "linux"), \
             mock.patch.object(picker, "which", return_value="/usr/bin/zenity"):
            self.assertFalse(picker.gui_available())              # brak DISPLAY
            with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
                self.assertTrue(picker.gui_available())

    def test_applescript_escapes_quotes_and_backslashes(self):
        s = picker.applescript("folder", 'Wybierz "folder" \\ kopii', "/Users/a b")
        self.assertIn('\\"folder\\"', s)
        self.assertIn("\\\\", s)
        self.assertIn("choose folder", s)
        self.assertIn("POSIX path of", s)
        self.assertIn("choose file", picker.applescript("file", "t", None))

    @unittest.skipUnless(sys.platform == "darwin" and __import__("shutil").which("osacompile"), "tylko macOS")
    def test_applescript_compiles(self):
        for kind in ("folder", "file"):
            s = picker.applescript(kind, 'Tytuł "z" cudzysłowem', "/Users")
            r = subprocess.run(["osacompile", "-o", os.devnull, "-e", s], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_powershell_script_quotes_single_quotes(self):
        s = picker.powershell_script("folder", "it's", r"C:\Users\O'Neil")
        self.assertIn("'it''s'", s)
        self.assertIn("O''Neil", s)
        self.assertIn("-STA" if False else "FolderBrowserDialog", s)

    def test_result_handling_ok_cancel_and_error_on_macos(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(picker, "_existing_dir", return_value="/"):
            with mock.patch.object(picker, "run", return_value=CmdResult(0, "/Users/me/Kopie/\n")):
                self.assertEqual(picker.pick_folder("t"), "/Users/me/Kopie")
            with mock.patch.object(picker, "run", return_value=CmdResult(1, "", "execution error: User canceled. (-128)")):
                self.assertIsNone(picker.pick_folder("t"))
            with mock.patch.object(picker, "run", return_value=CmdResult(1, "", "boom")):
                with self.assertRaises(picker.PickerUnavailable):
                    picker.pick_folder("t")

    def test_existing_dir_falls_back_to_nearest_parent(self):
        self.assertEqual(picker._existing_dir(os.path.join(os.path.expanduser("~"), "nie-istnieje-xyz", "a")),
                         os.path.expanduser("~"))
        self.assertIsNone(picker._existing_dir(None))


if __name__ == "__main__":
    unittest.main()

"""Interactive terminal UI (requires `pip install textual`)."""
import threading

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, ProgressBar, RichLog

from . import collect, report, tests


class DriveApp(App):
    TITLE = "Drive Info"
    BINDINGS = [("r", "refresh", "Refresh"), ("s", "speed", "Speed test"),
                ("c", "capacity", "Capacity test"), ("p", "report", "HTML report"),
                ("x", "cancel", "Cancel"), ("q", "quit", "Quit")]
    CSS = "DataTable{height:1fr} RichLog{height:12} ProgressBar{margin:0 1}"

    def __init__(self):
        super().__init__()
        self.devices = []
        self.results = {}
        self.busy = False
        self._cancel = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield ProgressBar(total=100, show_eta=False)
        yield RichLog(wrap=True, markup=False)
        yield Footer()

    def on_mount(self):
        t = self.query_one(DataTable)
        t.add_columns("Device", "Model", "Size", "Bus", "Type", "Removable", "SMART", "Mount")
        self.action_refresh()

    # ---- helpers
    def log_line(self, text):
        self.query_one(RichLog).write(text)

    def current(self):
        t = self.query_one(DataTable)
        if not self.devices or t.cursor_row >= len(self.devices):
            return None
        return self.devices[t.cursor_row]

    # ---- actions
    def action_refresh(self):
        self.devices = collect.collect()
        t = self.query_one(DataTable)
        t.clear()
        for d in self.devices:
            t.add_row(d["device"], d["model"], d["size"], d["bus"], d["kind"],
                      d["removable"], d["smart"], d["mount_points"].split(";")[0])
        self.log_line(f"{len(self.devices)} device(s) found.")

    def action_cancel(self):
        self._cancel = True

    def _start(self, kind):
        dev = self.current()
        if self.busy or not dev:
            return
        mount = next((m for m in dev["mount_points"].split(";") if m), None)
        if not mount:
            self.log_line(f"{dev['device']} is not mounted; cannot run tests.")
            return
        self.busy, self._cancel = True, False
        self.log_line(f"Running {kind} test on {dev['device']} ({mount})...")
        threading.Thread(target=self._work, args=(kind, dev, mount), daemon=True).start()

    def _work(self, kind, dev, mount):
        bar = self.query_one(ProgressBar)

        def progress(phase, frac, rate):
            self.call_from_thread(bar.update, progress=frac * 100)

        try:
            fn = tests.capacity_test if kind == "capacity" else tests.speed_test
            res = fn(mount, progress=progress, cancel=lambda: self._cancel)
            self.results.setdefault(dev["device"], []).append(res)
            lines = [f"  {k}: {v}" for k, v in res.items() if v not in ("", None)]
            self.call_from_thread(self.log_line, "\n".join(lines))
        except tests.Cancelled:
            self.call_from_thread(self.log_line, "Cancelled.")
        except Exception as e:
            self.call_from_thread(self.log_line, f"Error: {e}")
        finally:
            self.busy = False

    def action_speed(self):
        self._start("speed")

    def action_capacity(self):
        self._start("capacity")

    def action_report(self):
        dev = self.current()
        if not dev:
            return
        out = f"drive_report_{dev['device'].split('/')[-1]}.html"
        report.save(dev, self.results.get(dev["device"], []), out)
        self.log_line(f"Report saved: {out}")


def run():
    DriveApp().run()

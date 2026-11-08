"""Command line interface."""
import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

from . import collect, report, tests
from .collect import FIELDS


def print_table(devices):
    cols = ["device", "model", "size", "bus", "kind", "removable", "filesystem", "smart"]
    rows = [[str(d[c]) for c in cols] for d in devices]
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(cols)]

    def line(r):
        return "  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip()
    print(line(cols))
    print(line(["-" * w for w in widths]))
    for r in rows:
        print(line(r))


def select(devices, names, everything=False, external=False):
    if names:
        wanted = {Path(n).name for n in names}
        sel = [d for d in devices if Path(d["device"]).name in wanted]
        missing = wanted - {Path(d["device"]).name for d in sel}
        if missing:
            sys.exit(f"Device(s) not found: {', '.join(sorted(missing))}")
        return sel
    if everything:
        sel = [d for d in devices if not external or d["removable"] == "yes" or d["internal"] == "no"]
        if not sel:
            sys.exit("No matching devices.")
        return sel
    sys.exit("Specify a device (e.g. disk4 / sdb) or use --all.")


def mount_for(dev, override=None):
    if override:
        return override
    mounts = [m for m in dev["mount_points"].split(";") if m]
    if not mounts:
        sys.exit(f"{dev['device']} has no mounted volume; mount it or pass --mount PATH.")
    return mounts[0]


def progress_printer():
    def cb(phase, frac, rate):
        extra = f"  {rate:6.1f} MB/s" if rate else ""
        print(f"\r{phase:<10} {frac * 100:5.1f}%{extra}   ", end="", file=sys.stderr, flush=True)
    return cb


def run_test(kind, mount, args):
    try:
        if kind == "capacity":
            return tests.capacity_test(mount, args.limit_mb, progress_printer())
        return tests.speed_test(mount, args.size_mb, progress_printer())
    except (ValueError, tests.Cancelled) as e:
        sys.exit(f"\n{e}" if str(e) else "\nCancelled.")
    finally:
        print(file=sys.stderr)


def print_result(res):
    for k, v in res.items():
        if v not in ("", None):
            print(f"  {k}: {v}")


def cmd_list(a):
    devs = collect.collect()
    if not devs:
        sys.exit("No storage devices found.")
    print_table(devs)


def cmd_export(a):
    devs = select(collect.collect(), a.devices, a.all, a.external)
    stamp = datetime.datetime.now()
    out = a.output or f"drive_info_{stamp:%Y%m%d_%H%M%S}.{a.format}"
    fh = sys.stdout if out == "-" else open(out, "w", newline="", encoding="utf-8")
    try:
        if a.format == "json":
            json.dump(devs, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        else:
            w = csv.DictWriter(fh, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
            w.writeheader()
            w.writerows({k: d[k] for k in FIELDS} for d in devs)
    finally:
        if fh is not sys.stdout:
            fh.close()
    if out != "-":
        print(f"Saved {len(devs)} device(s) to: {out}", file=sys.stderr)


def cmd_test(a):
    dev = select(collect.collect(), [a.device])[0]
    res = run_test(a.cmd, mount_for(dev, a.mount), a)
    print_result(res)
    if a.cmd == "capacity" and res["errors"]:
        sys.exit(2)


def cmd_report(a):
    dev = select(collect.collect(), [a.device])[0]
    results = []
    mount = None
    if a.speed or a.capacity:
        mount = mount_for(dev, a.mount)
    if a.capacity:
        results.append(run_test("capacity", mount, a))
    if a.speed:
        results.append(run_test("speed", mount, a))
    out = a.output or f"drive_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.html"
    try:
        report.save(dev, results, out)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"Report saved to: {out}", file=sys.stderr)


def cmd_gui(a):
    from . import web
    web.serve(a.port, open_browser=not a.no_open)


def cmd_tui(a):
    try:
        from . import tui
    except ImportError:
        sys.exit("The terminal UI needs the 'textual' package:  pip install textual")
    tui.run()


def build_parser():
    ap = argparse.ArgumentParser(prog="drive_info.py",
                                 description="Inspect and test storage devices (SD cards, USB, SSD/HDD).")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("menu", help="interactive wizard (default in a terminal)").set_defaults(
        fn=lambda a: __import__("driveinfo.interactive", fromlist=["run"]).run())
    sub.add_parser("list", help="list detected devices").set_defaults(fn=cmd_list)

    e = sub.add_parser("export", help="export device info to CSV/JSON")
    e.add_argument("devices", nargs="*", metavar="DEVICE")
    e.add_argument("-a", "--all", action="store_true")
    e.add_argument("-e", "--external", action="store_true", help="with --all: only removable/external")
    e.add_argument("-f", "--format", choices=["csv", "json"], default="csv")
    e.add_argument("-o", "--output", help="file, or '-' for stdout")
    e.set_defaults(fn=cmd_export)

    for name, helptext in (("capacity", "capacity test / fake detector (writes then verifies free space)"),
                           ("speed", "speed test with SD class estimate")):
        t = sub.add_parser(name, help=helptext)
        t.add_argument("device")
        t.add_argument("--mount", help="mounted folder to test (default: device's first mount point)")
        t.add_argument("--limit-mb", type=int, help="capacity: only test this many MB")
        t.add_argument("--size-mb", type=int, default=256, help="speed: test file size (default 256)")
        t.set_defaults(fn=cmd_test)

    r = sub.add_parser("report", help="HTML/PDF report (optionally running tests first)")
    r.add_argument("device")
    r.add_argument("--speed", action="store_true", help="include speed test")
    r.add_argument("--capacity", action="store_true", help="include capacity test")
    r.add_argument("--mount")
    r.add_argument("--limit-mb", type=int)
    r.add_argument("--size-mb", type=int, default=256)
    r.add_argument("-o", "--output", help=".html or .pdf (PDF needs Chrome/Chromium)")
    r.set_defaults(fn=cmd_report)

    g = sub.add_parser("gui", help="browser GUI on localhost")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--no-open", action="store_true", help="do not open the browser")
    g.set_defaults(fn=cmd_gui)
    sub.add_parser("tui", help="interactive terminal UI (needs 'textual')").set_defaults(fn=cmd_tui)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from . import interactive
            return interactive.run()
        a = ap.parse_args(["list"])
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
    except RuntimeError as e:
        sys.exit(str(e))

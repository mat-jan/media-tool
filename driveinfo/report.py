"""HTML / PDF report for a device and its test results."""
import datetime
import html
import shutil
import subprocess
import tempfile
from pathlib import Path

LABELS = [
    ("device", "Device"), ("model", "Model"), ("name", "Name"), ("vendor", "Vendor"),
    ("serial", "Serial number"), ("serial_dec", "Serial (decimal)"), ("firmware", "Firmware"),
    ("size", "Capacity"), ("bus", "Bus"), ("kind", "Type"), ("removable", "Removable"),
    ("internal", "Internal"), ("filesystem", "Filesystem"), ("partitions", "Partitions"),
    ("mount_points", "Mount points"), ("smart", "SMART"), ("manufacturer_id", "Manufacturer ID"),
    ("mfg_date", "Manufacturing date"), ("vendor_id", "Vendor ID"), ("product_id", "Product ID"),
]
RESULT_LABELS = {
    "tested_mb": "Tested (MB)", "errors": "Errors", "first_bad_mb": "First bad block (MB)",
    "write_mb_s": "Write (MB/s)", "read_mb_s": "Read (MB/s)", "size_mb": "Test size (MB)",
    "seq_write_mb_s": "Sequential write (MB/s)", "seq_read_mb_s": "Sequential read (MB/s)",
    "rand_read_iops": "Random 4K read (IOPS)", "rand_write_iops": "Random 4K write (IOPS)",
    "speed_class": "Speed class", "app_class": "App class", "verdict": "Verdict", "note": "Note",
}
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--ok:#1a7f37;--bad:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f5f5f7;--mut:#a1a1a6;--line:#3a3a3c}}
body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:780px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.5rem;margin:0}h2{font-size:1.1rem;margin:1.8rem 0 .4rem}
.sub{color:var(--mut)}table{width:100%;border-collapse:collapse}
td{padding:.35rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
td:first-child{color:var(--mut);width:42%}.pass{color:var(--ok);font-weight:600}.fail{color:var(--bad);font-weight:600}
@media print{body{margin:0}}
"""


def _rows(pairs):
    return "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>"
                   for k, v in pairs if v not in ("", None))


def build_html(device, results=()):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"<h1>Drive report</h1><div class=sub>{html.escape(device.get('model') or device['device'])}"
             f" &middot; generated {now}</div>",
             "<h2>Device information</h2><table>",
             _rows((lbl, device.get(k, "")) for k, lbl in LABELS), "</table>"]
    for r in results:
        title = {"capacity": "Capacity test (fake detection)", "speed": "Speed test"}.get(r.get("test"), "Test")
        parts.append(f"<h2>{title}</h2><table>")
        for k, lbl in RESULT_LABELS.items():
            v = r.get(k)
            if v in ("", None):
                continue
            cell = html.escape(str(v))
            if k == "verdict":
                cell = f"<span class={'pass' if str(v).startswith('PASS') else 'fail'}>{cell}</span>"
                parts.append(f"<tr><td>{lbl}</td><td>{cell}</td></tr>")
            else:
                parts.append(_rows([(lbl, v)]))
        parts.append("</table>")
    return ("<!doctype html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Drive report</title><style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def _browser():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "microsoft-edge"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if Path(mac).exists() else None


def save(device, results, path):
    """Write HTML, or PDF when path ends in .pdf (needs Chrome/Chromium)."""
    doc = build_html(device, results)
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path.write_text(doc, encoding="utf-8")
        return path
    browser = _browser()
    if not browser:
        raise RuntimeError("PDF needs Chrome/Chromium installed; save as .html and print it instead")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "r.html"
        src.write_text(doc, encoding="utf-8")
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={path.resolve()}", src.as_uri()],
                       capture_output=True, timeout=60, check=True)
    return path

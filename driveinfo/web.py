"""Local browser GUI (stdlib only). Listens on 127.0.0.1 and requires a
random token, so other local pages/users cannot trigger writes to your drives."""
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import collect, report, tests

TOKEN = secrets.token_urlsafe(16)
JOBS = {}
LOCK = threading.Lock()

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Drive Info</title>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--acc:#0a63d6;--card:#f5f5f7;--ok:#1a7f37;--bad:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f5f5f7;--mut:#a1a1a6;--line:#3a3a3c;--acc:#4c9aff;--card:#2c2c2e}}
*{box-sizing:border-box}body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0}
main{max-width:900px;margin:0 auto;padding:1.2rem 16px}h1{font-size:1.4rem;margin:0 0 1rem}
.dev{border:1px solid var(--line);border-radius:10px;padding:.8rem 1rem;margin-bottom:.7rem;background:var(--card)}
.dev b{font-size:1.05rem}.mut{color:var(--mut);font-size:.9rem}
button{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:8px;padding:.35rem .8rem;margin:.5rem .4rem 0 0;cursor:pointer}
button:hover{border-color:var(--acc)}button:disabled{opacity:.4;cursor:default}
details{margin-top:.5rem}table{border-collapse:collapse;width:100%}td{padding:.2rem .4rem;border-bottom:1px solid var(--line);font-size:.9rem}td:first-child{color:var(--mut);width:40%}
progress{width:100%;margin-top:.5rem}.res{margin-top:.6rem;font-size:.9rem;white-space:pre-wrap}.pass{color:var(--ok)}.fail{color:var(--bad)}
</style></head><body><main><h1>Drive Info</h1><div id=list class=mut>Loading...</div>
<p class=mut>Tests write temporary files to the mounted volume and delete them afterwards.</p></main>
<script>
const T=new URLSearchParams(location.search).get('t');
const api=(p,o={})=>fetch(p+(p.includes('?')?'&':'?')+'t='+T,o).then(r=>r.json());
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let devices=[];
async function load(){devices=await api('/api/devices');render()}
function render(){
 const el=document.getElementById('list');
 if(!devices.length){el.textContent='No devices found.';return}
 el.innerHTML=devices.map((d,i)=>`<div class=dev id=d${i}><b>${esc(d.model||d.device)}</b>
  <span class=mut> ${esc(d.device)} &middot; ${esc(d.size)} &middot; ${esc(d.bus)} ${esc(d.kind)}</span>
  <div class=mut>${d.mount_points?'Mounted: '+esc(d.mount_points):'Not mounted - tests unavailable'}</div>
  <details><summary>All details</summary><table>${Object.entries(d).filter(([k,v])=>v!=='').map(([k,v])=>`<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('')}</table></details>
  <button ${d.mount_points?'':'disabled'} onclick="run(${i},'speed')">Speed test</button>
  <button ${d.mount_points?'':'disabled'} onclick="run(${i},'capacity')">Capacity test (fake detector)</button>
  <button onclick="rep(${i})">Report (HTML)</button>
  <div class=out></div></div>`).join('')}
async function run(i,test){
 const out=document.querySelector('#d'+i+' .out');
 out.innerHTML='<progress value=0 max=1></progress><div class=mut>starting...</div>';
 const j=await api('/api/run',{method:'POST',body:JSON.stringify({device:devices[i].device,test})});
 if(j.error){out.textContent=j.error;return}
 const timer=setInterval(async()=>{
  const s=await api('/api/job/'+j.id);
  if(s.status==='running'){out.innerHTML=`<progress value=${s.frac} max=1></progress><div class=mut>${s.phase} ${s.rate?s.rate.toFixed(1)+' MB/s':''}</div><button onclick="cancel('${j.id}')">Cancel</button>`}
  else{clearInterval(timer);
   if(s.status==='error'){out.textContent='Error: '+s.error;return}
   const r=s.result;devices[i].results=(devices[i].results||[]).concat([r]);
   const cls=r.verdict?(r.verdict.startsWith('PASS')?'pass':'fail'):'';
   out.innerHTML='<div class="res '+cls+'">'+esc(Object.entries(r).filter(([k,v])=>v!==''&&v!==null).map(([k,v])=>k+': '+v).join('\\n'))+'</div>'}
 },700)}
function cancel(id){api('/api/cancel/'+id,{method:'POST'})}
function rep(i){window.open('/report?t='+T+'&device='+encodeURIComponent(devices[i].device)+'&r='+encodeURIComponent(JSON.stringify(devices[i].results||[])))}
load();
</script></body></html>"""


def start_job(device, kind):
    dev = next((d for d in collect.collect() if d["device"] == device), None)
    if not dev:
        return {"error": "device not found"}
    mounts = [m for m in dev["mount_points"].split(";") if m]
    if not mounts:
        return {"error": "device is not mounted"}
    job = {"id": secrets.token_hex(6), "status": "running", "phase": "", "frac": 0.0,
           "rate": 0.0, "cancel": False, "result": None, "error": ""}
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            return {"error": "another test is already running"}
        JOBS[job["id"]] = job

    def progress(phase, frac, rate):
        job.update(phase=phase, frac=frac, rate=rate)

    def work():
        try:
            fn = tests.capacity_test if kind == "capacity" else tests.speed_test
            job["result"] = fn(mounts[0], progress=progress, cancel=lambda: job["cancel"])
            job["status"] = "done"
        except tests.Cancelled:
            job.update(status="error", error="cancelled")
        except Exception as e:  # report to the UI instead of dying silently
            job.update(status="error", error=str(e))
    threading.Thread(target=work, daemon=True).start()
    return {"id": job["id"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, q):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost") and q.get("t", [""])[0] == TOKEN

    def _handle(self, method):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authorized(q):
            return self._send(json.dumps({"error": "forbidden"}), code=403)
        if method == "GET" and u.path == "/":
            return self._send(PAGE, "text/html")
        if method == "GET" and u.path == "/api/devices":
            return self._send(json.dumps(collect.collect()))
        if method == "GET" and u.path.startswith("/api/job/"):
            j = JOBS.get(u.path.rsplit("/", 1)[1])
            if not j:
                return self._send(json.dumps({"error": "unknown job"}), code=404)
            return self._send(json.dumps({k: v for k, v in j.items() if k != "cancel"}))
        if method == "GET" and u.path == "/report":
            dev = next((d for d in collect.collect() if d["device"] == q.get("device", [""])[0]), None)
            if not dev:
                return self._send("device not found", "text/plain", 404)
            try:
                results = json.loads(q.get("r", ["[]"])[0])
            except ValueError:
                results = []
            return self._send(report.build_html(dev, results), "text/html")
        if method == "POST" and u.path == "/api/run":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if body.get("test") not in ("speed", "capacity"):
                return self._send(json.dumps({"error": "bad test"}), code=400)
            return self._send(json.dumps(start_job(body.get("device", ""), body["test"])))
        if method == "POST" and u.path.startswith("/api/cancel/"):
            j = JOBS.get(u.path.rsplit("/", 1)[1])
            if j:
                j["cancel"] = True
            return self._send(json.dumps({"ok": True}))
        self._send(json.dumps({"error": "not found"}), code=404)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def serve(port=8765, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    print(f"Drive Info GUI: {url}\nCtrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

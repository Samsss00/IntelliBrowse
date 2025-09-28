# app/main.py
from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import os, time, json
import concurrent.futures as futures  # timeout wrapper

from app.agent.planner import plan_from_query
from app.agent.executor import execute_plan
from app.utils.logger import logger
from app.utils.export import save_results  # CSV/JSON export
from app.browser.controller import BrowserController  # for /readyz
from app.config.settings import RUNS_DIR  # where artifacts go
from app.utils.score import enrich_results  # server-side why/score enrichment

# configurable hard timeout for /run
RUN_TIMEOUT_SEC = int(os.getenv("RUN_TIMEOUT_SEC", "90"))

app = FastAPI(title="Web Navigator AI Agent", version="1.1.0")

# Serve /static (expects files in app/static)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Favicon ----------
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

# ---------- Helpers ----------
def _is_writable_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".probe_{int(time.time()*1000)}")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False

def _fallback_dir() -> str:
    # Prefer RUNS_DIR if writable, else /tmp
    return RUNS_DIR if _is_writable_dir(RUNS_DIR) else "/tmp"

def _history_path() -> str:
    base = _fallback_dir()
    return os.path.join(base, "_history.jsonl")

def _append_history(entry: Dict[str, Any]) -> None:
    try:
        path = _history_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to append history")

def _read_history(limit: int = 25) -> List[Dict[str, Any]]:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        lines = lines[-max(1, min(limit, 200)):]
        out: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return list(reversed(out))
    except Exception:
        logger.exception("Failed to read history")
        return []

# ---------- Health / readiness ----------

@app.get("/healthz")
def healthz():
    """
    Liveness & write check with safe fallback:
    - Tries RUNS_DIR, falls back to /tmp if mounted volume blocks writes (common on Windows).
    """
    try:
        writable = _is_writable_dir(RUNS_DIR)
        target = RUNS_DIR if writable else "/tmp"
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, f"healthz_{int(time.time())}.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return {"ok": True, "writable": writable, "runs_dir": RUNS_DIR, "write_dir": target}
    except Exception as e:
        logger.exception("Healthz failed")
        return {"ok": False, "writable": False, "error": str(e), "runs_dir": RUNS_DIR, "write_dir": None}

@app.get("/readyz")
def readyz():
    """
    Browser readiness probe: launches Chromium & saves a screenshot.
    Write the screenshot to a path that is known-writable.
    """
    try:
        rd = _fallback_dir()
        abs_path = os.path.join(rd, "readyz.png")
        with BrowserController(run_dir=rd) as bc:
            _ = bc.screenshot(abs_path)
        return {"ready": True, "run_dir_used": rd, "screenshot": abs_path}
    except Exception as e:
        logger.exception("Readyz failed")
        return {"ready": False, "error": str(e)}

# ---------- /run API ----------

class RunRequest(BaseModel):
    query: str = Field(..., description="Natural language instruction from user")
    max_results: int = Field(8, ge=1, le=20)
    # Optional overrides
    site: Optional[str] = None
    budget: Optional[int] = None
    min_price: Optional[int] = None
    include: Optional[str] = None
    exclude: Optional[str] = None

class RunResponse(BaseModel):
    ok: bool
    query: str
    plan: Dict[str, Any]
    results: List[Dict[str, Any]]
    artifacts: Dict[str, Any]

@app.post("/run", response_model=RunResponse)
def run_agent(req: RunRequest):
    logger.info(f"Received query: {req.query}")
    plan = plan_from_query(req.query, req.max_results)
    step = plan["plan"][0]

    # Apply simple overrides if present
    if req.site:
        step["site"] = req.site
    if req.budget is not None:
        step["max_price"] = req.budget
        q = step.get("query") or "laptops"
        if "under" not in q.lower():
            step["query"] = f"{q} under {req.budget}"
    if req.min_price is not None:
        step["min_price"] = req.min_price
    if req.include:
        step["include"] = req.include
    if req.exclude:
        step["exclude"] = req.exclude

    logger.info(f"PLAN:\n {json.dumps(plan, indent=2, ensure_ascii=False)}\n")

    # Run the plan with a hard timeout so the API never hangs forever
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(execute_plan, plan)
        try:
            results, artifacts = fut.result(timeout=RUN_TIMEOUT_SEC)

            # Enrich results with why + score (and derived pros/cons on server if you added it there)
            results = enrich_results(results, budget=req.budget)

            # Append run to history (best-effort)
            try:
                _append_history({
                    "ts": int(time.time()),
                    "query": req.query,
                    "site": step.get("site"),
                    "max_results": step.get("max_results"),
                    "min_price": step.get("min_price"),
                    "max_price": step.get("max_price"),
                    "count": len(results or []),
                    "run_dir": artifacts.get("run_dir"),
                    "last_url": artifacts.get("last_url"),
                    "steps": artifacts.get("steps", []),
                })
            except Exception:
                pass

            return RunResponse(ok=True, query=req.query, plan=plan, results=results, artifacts=artifacts)
        except futures.TimeoutError:
            logger.error(f"/run timed out after {RUN_TIMEOUT_SEC}s")
            return RunResponse(
                ok=False,
                query=req.query,
                plan=plan,
                results=[],
                artifacts={"error": f"Timed out after {RUN_TIMEOUT_SEC}s"}
            )
        except Exception as e:
            logger.exception("Agent run failed")
            raise HTTPException(status_code=500, detail=str(e))

# ---------- /export API ----------

class ExportRequest(BaseModel):
    query: str
    max_results: int = 8
    site: Optional[str] = None
    budget: Optional[int] = None
    min_price: Optional[int] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    fmt: str = "csv"  # "csv" | "json"

@app.post("/export")
def export_run(req: ExportRequest):
    try:
        plan = plan_from_query(req.query, req.max_results)
        step = plan["plan"][0]
        if req.site:
            step["site"] = req.site
        if req.budget is not None:
            step["max_price"] = req.budget
            q = step.get("query") or "laptops"
            if "under" not in q.lower():
                step["query"] = f"{q} under {req.budget}"
        if req.min_price is not None:
            step["min_price"] = req.min_price
        if req.include:
            step["include"] = req.include
        if req.exclude:
            step["exclude"] = req.exclude

        results, artifacts = execute_plan(plan)

        # Enrich before saving so CSV/JSON is ranked & self-explanatory
        results = enrich_results(results, budget=req.budget)

        saved = save_results(artifacts["run_dir"], results)

        fmt = (req.fmt or "csv").lower()
        if fmt not in {"csv", "json"}:
            fmt = "csv"

        path = saved["csv"] if fmt == "csv" else saved["json"]
        filename = path.replace("\\", "/").split("/")[-1]
        media = "text/csv" if fmt == "csv" else "application/json"
        return FileResponse(path, media_type=media, filename=filename)
    except Exception as e:
        logger.exception("Export run failed")
        raise HTTPException(status_code=500, detail=str(e))

# ---------- History API ----------

@app.get("/history")
def get_history(limit: int = Query(25, ge=1, le=200)):
    """
    Returns the most recent runs (newest first) from _history.jsonl
    """
    try:
        items = _read_history(limit=limit)
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        logger.exception("/history failed")
        return JSONResponse({"ok": False, "error": str(e)})

@app.delete("/history")
def clear_history():
    """
    Clears history log (best-effort). Does not delete per-run directories.
    """
    try:
        path = _history_path()
        if os.path.exists(path):
            os.remove(path)
        return {"ok": True}
    except Exception as e:
        logger.exception("clear_history failed")
        return {"ok": False, "error": str(e)}

# ---------- /demo: responsive UI with Compare, Why, Pros/Cons ----------

@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Web Navigator — Demo</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg: #0b0d13; --bg2: #0c1220; --panel: #0f1422; --muted:#a3b1c6; --text:#eaf1ff; --border:#1f2a44;
      --brand1:#7c3aed; --brand2:#2563eb; --card:#10172a; --chip:#0d203d; --chip-border:#1d3b6b; --overlay: rgba(0,0,0,.55);
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{margin:0;background:
      radial-gradient(1200px 600px at 10% -10%, rgba(124,58,237,.18), transparent),
      radial-gradient(1000px 600px at 90% 10%, rgba(37,99,235,.18), transparent),
      linear-gradient(180deg, var(--bg), var(--bg2));
      color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,Inter,Roboto,Arial}
    a{color:#cfe2ff}
    .container{max-width:100%; width:100%; margin:0 auto; padding:24px}
    .brand{display:flex;align-items:center;gap:12px}
    .logo{width:36px;height:36px;border-radius:8px;object-fit:cover;box-shadow:0 6px 20px rgba(0,0,0,.35)}
    h1{margin:0;font-size:22px;letter-spacing:.3px}
    .sub{color:var(--muted);font-size:12px}
    .shell{margin-top:14px;background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:16px;backdrop-filter: blur(6px);box-shadow:0 20px 60px rgba(0,0,0,.35)}
    .bar{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)}
    .controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
    .chip{display:inline-flex;align-items:center;gap:6px;background:var(--chip);border:1px solid var(--chip-border);color:#c7ddff;padding:6px 10px;border-radius:999px;font-size:12px}
    label{font-size:12px;color:var(--muted);display:block;margin-bottom:6px}
    input,select,button{padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#0b1324;color:#0fe0ff}
    input,select{min-width:120px}
    input[type="number"]{min-width:110px}
    .grow{flex:1}
    .btn{cursor:pointer;border:0;background:linear-gradient(90deg,var(--brand1),var(--brand2));box-shadow:0 8px 20px rgba(124,58,237,.35);color:#fff}
    .btn.ghost{background:transparent;border:1px solid var(--border);box-shadow:none;color:#cfe2ff}
    .status{padding:6px 10px;border-radius:999px;background:#0c1220;border:1px solid var(--border);color:#cfe2ff;font-size:12px}
    .grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:14px;padding:16px}
    .card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px;transform:translateZ(0)}
    .card:hover{box-shadow:0 18px 40px rgba(0,0,0,.35);transform:translateY(-1px);transition:all .25s ease}
    .title{font-weight:600}
    .price{font-weight:700}
    .row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
    .chips{display:flex;gap:6px;flex-wrap:wrap}
    .meta{color:var(--muted);font-size:12px}
    .why{color:#b8c3d6;font-size:12px}
    .table-wrap{padding:0 16px 16px}
    table{width:100%;border-collapse:separate;border-spacing:0;min-width:780px;border:1px solid var(--border);border-radius:12px;overflow:hidden}
    thead th{position:sticky;top:0;background:#0b1324;border-bottom:1px solid var(--border);text-align:left;font-size:12px;color:#a3b1c6;padding:10px}
    tbody td{padding:12px 10px;border-bottom:1px solid var(--border)}
    tbody tr:hover{background:#0c162a}
    .idx{color:#9fb3d9;font-variant-numeric:tabular-nums}
    .pill{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;border:1px solid var(--border);background:#0c1220;color:#b8c3d6}
    .footer{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-top:1px solid var(--border);color:#a3b1c6;font-size:12px}
    .switch{display:flex;gap:8px;align-items:center}
    .switch input{accent-color:#7c3aed}
    .toast{position:fixed;right:18px;bottom:18px;background:#0b1324;border:1px solid var(--border);color:#dbe7ff;padding:10px 12px;border-radius:10px;box-shadow:0 12px 30px rgba(0,0,0,.3);opacity:0;transform:translateY(8px);transition:all .25s ease}
    .toast.show{opacity:1;transform:translateY(0)}
    .skeleton{height:120px;border-radius:12px;background:linear-gradient(90deg, #111a2c 25%, #0e1628 37%, #111a2c 63%); background-size:400% 100%; animation:sheen 1.2s ease-in-out infinite}
    @keyframes sheen{0%{background-position:100% 0}100%{background-position:0 0}}
    .hidden{display:none !important}
    .layout{display:grid;grid-template-columns:minmax(0,1fr) 320px; gap:16px; align-items:start;}
    .side{background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:14px;overflow:hidden}
    .side h3{margin:0;padding:12px 14px;border-bottom:1px solid var(--border);font-size:14px}
    .side .body{padding:10px}
    .hist{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow:auto}
    .hist .item{display:flex;flex-direction:column;gap:6px;border:1px solid var(--border);border-radius:12px;padding:10px;background:#0b1324}
    .hist .meta{display:flex;gap:10px;flex-wrap:wrap;color:#9fb3d9;font-size:12px}
    .hist .q{font-size:13px}
    .tray{position:fixed;left:16px;bottom:16px;right:16px;display:flex;justify-content:space-between;align-items:center;gap:10px;background:#0b1324;border:1px solid var(--border);border-radius:14px;padding:10px 12px;box-shadow:0 16px 40px rgba(0,0,0,.35)}
    .overlay{position:fixed;inset:0;background:var(--overlay);display:none;align-items:center;justify-content:center}
    .overlay.show{display:flex}
    .modal{width:min(1040px,92vw);max-height:80vh;overflow:auto;background:#0b1324;border:1px solid var(--border);border-radius:14px;padding:16px;box-shadow:0 24px 60px rgba(0,0,0,.5)}
    .modal h3{margin:0 0 8px}
    .kbd{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;font-size:12px;background:#0a1122;border:1px solid #17213b;border-radius:6px;padding:1px 6px;color:#cfe2ff}
    @media (max-width:1100px){ .layout{grid-template-columns:1fr} .side{order:-1} .tray{left:12px; right:12px} }
    .best { outline: 1px solid #2e7d32; background: rgba(46,125,50,.12); border-radius: 8px; }
    .dim { opacity:.7 }
    .diff-toggle { display:flex; align-items:center; gap:8px }
    .cell { padding:8px; border-bottom:1px solid var(--border); vertical-align:top }
    .spec-label { font-weight:600; white-space:nowrap }
    .comp-table { width:100%; border-collapse:separate; border-spacing:0; border:1px solid var(--border); border-radius:12px; overflow:hidden }
    .comp-table thead th { background:#0b1324; position:sticky; top:0; padding:10px; text-align:left; font-size:12px; color:#a3b1c6; border-bottom:1px solid var(--border) }
    .comp-table tbody td, .comp-table tbody th { padding:10px; border-bottom:1px solid var(--border) }
    .badge-mini { display:inline-block; padding:2px 6px; border-radius:999px; font-size:11px; border:1px solid var(--border); background:#0c1220; color:#b8c3d6 }
  </style>
</head>
<body>
  <div class="container">
    <div class="brand">
      <img class="logo" src="/static/logo.png" alt="Web Navigator logo" onerror="this.style.display='none'">
      <div>
        <h1>Web Navigator</h1>
        <div class="sub">Shareable URLs • Shortlist + Compare</div>
      </div>
    </div>

    <div class="layout">
      <div class="shell">
        <div class="bar">
          <div class="controls">
            <div class="grow">
              <label>Query</label>
              <input id="q" class="grow" value="Find 8 laptops under 50k on Flipkart" />
            </div>
            <div>
              <label>Site</label>
              <select id="site">
                <option value="">Auto</option>
                <option value="flipkart">Flipkart</option>
                <option value="amazon">Amazon</option>
              </select>
            </div>
            <div>
              <label>Max</label>
              <input id="max" type="number" value="8" min="1" max="20">
            </div>
            <div>
              <label>Budget (₹)</label>
              <input id="budget" type="number" placeholder="50000">
            </div>
            <div>
              <label>Min (₹)</label>
              <input id="min_price" type="number" placeholder="30000">
            </div>
          </div>
          <div class="controls">
            <button class="btn" onclick="run()">Run</button>
            <button class="btn ghost" onclick="exportFile('csv')">Export CSV</button>
            <button class="btn ghost" onclick="openCompareAll()">Compare All (Specs)</button>
            <span id="status" class="status">idle</span>
          </div>
        </div>

        <div class="bar" style="border-top:1px solid var(--border);">
          <div class="controls">
            <span class="chip" id="modeLabel">Grid View</span>
            <div class="switch">
              <input type="checkbox" id="viewToggle" onchange="toggleView()"> <label for="viewToggle" class="sub">Table mode</label>
            </div>
            <div class="switch">
              <input type="checkbox" id="badgeToggle" checked> <label for="badgeToggle" class="sub">Spec badges</label>
            </div>
          </div>
        </div>

        <!-- GRID RESULTS -->
        <div id="gridWrap" class="grid" aria-live="polite"></div>

        <!-- TABLE RESULTS -->
        <div id="tableWrap" class="table-wrap hidden">
          <table id="tbl">
            <thead id="thead"></thead>
            <tbody id="tbody"></tbody>
          </table>
        </div>

        <div class="footer">
          <span id="lastUrl" class="sub"></span>
          <div class="controls">
            <a class="chip" href="/healthz" target="_blank" rel="noopener">/healthz</a>
            <a class="chip" href="/readyz" target="_blank" rel="noopener">/readyz</a>
          </div>
        </div>
      </div>

      <div class="side">
        <h3>Recent Runs</h3>
        <div class="body">
          <div style="display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap;">
            <button class="btn ghost" onclick="loadHistory()">Refresh</button>
            <button class="btn ghost" onclick="clearHistory()">Clear</button>
            <button class="btn ghost" onclick="copyShareURL()">Copy Share URL</button>
          </div>
          <div id="hist" class="hist">
            <div class="sub">No history yet. Run a query to see it here.</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Shortlist tray -->
  <div id="tray" class="tray hidden">
    <div><strong>Shortlist:</strong> <span id="trayCount">0</span> item(s)</div>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <button class="btn ghost" onclick="openCompare()">Compare</button>
      <button class="btn ghost" onclick="copyShortlist()">Copy</button>
      <button class="btn ghost" onclick="downloadShortlist()">Download</button>
      <button class="btn ghost" onclick="clearShortlist()">Clear</button>
    </div>
  </div>

  <!-- Compare modal -->
  <div id="overlay" class="overlay" onclick="closeOverlay(event)">
    <div class="modal" onclick="event.stopPropagation()">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h3 id="compareTitle">Compare Shortlist</h3>
        <button class="btn ghost" onclick="closeOverlay()">Close</button>
      </div>
      <div id="compareWrap" style="overflow:auto"></div>
      <div class="sub" style="margin-top:8px">Tip: Use <span class="kbd">Copy</span> to share this selection.</div>
    </div>
  </div>

  <div id="toast" class="toast">Hello</div>

<script>
let lastItems = [];
let lastQuery = '';
let laptopMode = false;
let gridView = true;
let shortlist = new Map(); // key=link, value=item

// ---------- Utilities ----------
function toast(msg){
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'), 2000);
}
function formatINR(n){
  if(n == null) return 'N/A';
  try { return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(n); }
  catch(e){ return '₹' + n; }
}
function setParams(obj){
  const p = new URLSearchParams(location.search);
  Object.entries(obj).forEach(([k,v])=>{
    if(v===null || v===undefined || v==='') p.delete(k);
    else p.set(k,String(v));
  });
  history.replaceState(null,'', location.pathname + '?' + p.toString());
}
function copy(text){
  navigator.clipboard.writeText(text).then(()=>toast('Copied')).catch(()=>toast('Copy failed'));
}
function saveJSON(filename, data){
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
function toGB(v){ if(v == null || v==='') return null; const n = Number(v); return Number.isFinite(n) ? n : null; }

// ---------- Laptop heuristics & badges ----------
function looksLikeLaptopQuery(q){
  const s = (q||'').toLowerCase();
  const laptopWords = ['laptop','laptops','notebook','ultrabook','gaming laptop','macbook'];
  return laptopWords.some(w => s.includes(w));
}
function inferLaptopFromResults(items){
  if(!Array.isArray(items) || !items.length) return false;
  const titles = items.map(r => (r.title||'').toLowerCase());
  const hits = titles.filter(t => t.includes('laptop') || t.includes('notebook') || t.includes('macbook')).length;
  return hits >= Math.max(1, Math.ceil(titles.length * 0.35));
}
function specBadgesAllowed(){
  const wantBadges = document.getElementById('badgeToggle').checked;
  return wantBadges && laptopMode;
}
function badge(text){ if(!text) return ''; return `<span class="chip">${text}</span>`; }
function badgeRow(r){
  if(!specBadgesAllowed()) return '';
  const b = [];
  const ram = toGB(r.ram_gb); if(ram) b.push(badge(ram+'GB RAM'));
  if(r.cpu) b.push(badge(r.cpu));
  if(r.gpu) b.push(badge(r.gpu));
  if(r.os) b.push(badge((''+r.os).toUpperCase()));
  const scr = Number(r.screen_inches); if(Number.isFinite(scr)) b.push(badge(scr+'"'));
  return b.length ? `<div class="chips">${b.join('')}</div>` : '';
}

// ---------- Why choose ----------
function cpuTier(cpu){
  const s = (cpu||'').toLowerCase();
  if(!s) return 0;
  if(s.includes('i9') || s.includes('ryzen 9') || s.includes('m3 max') || s.includes('m3 pro')) return 5;
  if(s.includes('i7') || s.includes('ryzen 7') || s.includes('m3') || s.includes('m2 pro')) return 4;
  if(s.includes('i5') || s.includes('ryzen 5') || s.includes('m2') || s.includes('m1 pro')) return 3;
  if(s.includes('i3') || s.includes('ryzen 3') || s.includes('m1')) return 2;
  return 1;
}
function hasDiscreteGPU(gpu){
  const s = (gpu||'').toLowerCase();
  return s.includes('rtx') || s.includes('gtx') || s.includes('radeon');
}
function whyChoose(row){
  const bits = [];
  const price = Number(row.price_value ?? '');
  if(Number.isFinite(price)){
    if(price < 35000) bits.push('great value');
    else if(price < 50000) bits.push('good price');
    else bits.push('premium tier');
  }
  const ram = Number(row.ram_gb ?? '');
  if(Number.isFinite(ram)){
    if(ram >= 16) bits.push(`${ram}GB RAM`);
    else if(ram >= 8) bits.push('8GB RAM');
  }
  const cpu = row.cpu || '';
  if(cpu){
    const nice = ['i9','i7','i5','i3','Ryzen 9','Ryzen 7','Ryzen 5','Ryzen 3','M3','M2','M1'];
    const hit = nice.find(t => cpu.toLowerCase().includes(t.toLowerCase()));
    bits.push(hit || cpu.split(' ')[0]);
  }
  const ssd = Number(row.storage_ssd_gb ?? '');
  if(Number.isFinite(ssd)){
    if(ssd >= 512) bits.push('512GB+ SSD');
    else if(ssd >= 256) bits.push('256GB SSD');
  }
  if(hasDiscreteGPU(row.gpu)) bits.push('discrete GPU');
  if(row.os) bits.push(String(row.os).toUpperCase());
  const scr = Number(row.screen_inches ?? '');
  if(Number.isFinite(scr)) bits.push(scr >= 15 ? '15"+ screen' : '~14" screen');
  if(row.brand) bits.push(row.brand);
  const seen = new Set(), out = [];
  for(const b of bits){ const k = b.toLowerCase(); if(!seen.has(k)){ seen.add(k); out.push(b); } }
  return out.slice(0,6).join(' • ');
}

// ---------- Pros / Cons (client mirror of server) ----------
function buildProsCons(row){
  const pros = [], cons = [];
  const price = Number(row.price_value ?? '');
  if(Number.isFinite(price)){
    if(price < 35000) pros.push('Low price');
    else if(price > 80000) cons.push('Expensive');
  }
  const ram = Number(row.ram_gb ?? '');
  if(Number.isFinite(ram)){
    if(ram >= 16) pros.push('16GB+ RAM');
    else if(ram < 8) cons.push('Under 8GB RAM');
  }
  const ssd = Number(row.storage_ssd_gb ?? '');
  if(Number.isFinite(ssd)){
    if(ssd >= 512) pros.push('512GB+ SSD');
    else if(ssd < 256) cons.push('Small SSD (<256GB)');
  } else {
    cons.push('SSD unknown');
  }
  const tier = cpuTier(row.cpu || '');
  if(tier >= 4) pros.push('High-tier CPU');
  else if((row.cpu||'') && tier <= 1) cons.push('Entry-level CPU');

  if(row.gpu){
    if(hasDiscreteGPU(row.gpu)) pros.push('Discrete GPU');
    else cons.push('Integrated graphics');
  }
  const os = (row.os||'').toString().toLowerCase();
  if(os.includes('windows 11') || os.includes('mac')) pros.push('Modern OS');
  else if(os) cons.push('Older OS');

  const scr = Number(row.screen_inches ?? '');
  if(Number.isFinite(scr)){
    if(15 <= scr && scr <= 16) pros.push('Comfortable 15–16" screen');
    else if(scr < 14) pros.push('Portable ~14"');
    else if(scr > 16) cons.push('Large & less portable');
  }
  const dedup = (arr)=>{ const s=new Set(), out=[]; for(const x of arr){ const k=x.toLowerCase(); if(!s.has(k)){ s.add(k); out.push(x); if(out.length>=6) break; } } return out; };
  return { pros: dedup(pros), cons: dedup(cons) };
}

// ---------- Scoring & comparison ----------
function scoreItem(r, prefs={}){
  const w = { price:0.35, ram:0.20, cpu:0.20, ssd:0.12, gpu:0.08, screen:0.03, os:0.02 };
  let price = Number(r.price_value ?? '');
  if(!Number.isFinite(price)) price = 999999;
  const budget = Number(document.getElementById('budget').value || '');
  let priceScore;
  if(Number.isFinite(budget) && budget > 0){
    if(price <= budget) { priceScore = 1 - (price / Math.max(budget, 1)); priceScore = Math.min(1, priceScore + 0.05); }
    else { priceScore = Math.max(0, 0.15 - (price - budget) / (budget * 2)); }
  } else {
    priceScore = Math.max(0, Math.min(1, (150000 - price) / 130000));
  }

  const ram = Number(r.ram_gb ?? '');
  let ramScore = 0;
  if(Number.isFinite(ram)){
    if(ram >= 32) ramScore = 1;
    else if(ram >= 16) ramScore = 0.8;
    else if(ram >= 8) ramScore = 0.5;
    else ramScore = 0.2;
  }

  const cpuScore = Math.min(1, cpuTier(r.cpu) / 5);

  const ssd = Number(r.storage_ssd_gb ?? '');
  let ssdScore = 0;
  if(Number.isFinite(ssd)){
    if(ssd >= 1024) ssdScore = 1;
    else if(ssd >= 512) ssdScore = 0.8;
    else if(ssd >= 256) ssdScore = 0.5;
    else ssdScore = 0.2;
  }

  const gpuScore = hasDiscreteGPU(r.gpu) ? 1 : 0.3;

  const scr = Number(r.screen_inches ?? '');
  let screenScore = 0.6;
  if(Number.isFinite(scr)){
    if(scr >= 15 && scr <= 16) screenScore = 1;
    else if(scr >= 13 && scr < 15) screenScore = 0.8;
    else screenScore = 0.5;
  }

  const os = (r.os||'').toString().toLowerCase();
  let osScore = 0.5;
  if(os.includes('windows 11') || os.includes('mac')) osScore = 1;
  else if(os.includes('windows')) osScore = 0.8;

  const score = (
    w.price*priceScore + w.ram*ramScore + w.cpu*cpuScore +
    w.ssd*ssdScore + w.gpu*gpuScore + w.screen*screenScore + w.os*osScore
  );
  return Math.round(score * 1000) / 1000;
}

// ---------- Shortlist ----------
function updateTray(){
  const tray = document.getElementById('tray');
  const count = shortlist.size;
  document.getElementById('trayCount').textContent = count;
  tray.classList.toggle('hidden', count===0);
}
function toggleSave(item){
  const key = item.link || item.title || Math.random().toString(36).slice(2);
  if(shortlist.has(key)){ shortlist.delete(key); } else { shortlist.set(key, item); }
  updateTray();
}
function copyShortlist(){
  const data = Array.from(shortlist.values());
  copy(JSON.stringify(data, null, 2));
}
function downloadShortlist(){
  const data = Array.from(shortlist.values());
  saveJSON('shortlist.json', data);
}
function clearShortlist(){
  shortlist.clear();
  updateTray();
}

// ---------- Compare modal (spec-by-spec) ----------
function fmt(v, key){
  if(v==null || v==='') return '—';
  if(key==='price' || key==='price_value') return (typeof v==='number') ? formatINR(v) : (v || '—');
  if(key==='ram_gb') return Number.isFinite(Number(v)) ? `${Number(v)} GB` : v;
  if(key==='storage_ssd_gb' || key==='storage_hdd_gb') return Number.isFinite(Number(v)) ? `${Number(v)} GB` : v;
  if(key==='screen_inches') return Number.isFinite(Number(v)) ? `${Number(v)}"` : v;
  if(key==='os') return String(v).toUpperCase();
  return v;
}
function buildSpecRows(items){
  const cols = [
    { key:'title',         label:'Model' },
    { key:'price_value',   label:'Price' },
    { key:'ram_gb',        label:'RAM' },
    { key:'storage_ssd_gb',label:'SSD' },
    { key:'storage_hdd_gb',label:'HDD' },
    { key:'cpu',           label:'CPU' },
    { key:'gpu',           label:'GPU' },
    { key:'os',            label:'OS' },
    { key:'screen_inches', label:'Screen' },
    { key:'brand',         label:'Brand' },
    { key:'source',        label:'Source' },
    { key:'link',          label:'Open' },
  ];
  const rows = cols.map(col => {
    const values = items.map(it => {
      if(col.key==='title'){
        const t = it.title || '';
        const href = it.link || '#';
        const short = t.length > 64 ? t.slice(0,64)+'…' : t;
        return `<a href="${href}" target="_blank" rel="noopener">${short}</a>`;
      }
      if(col.key==='link'){
        const href = it.link || '#';
        return href && href!=='#' ? `<a class="badge-mini" href="${href}" target="_blank" rel="noopener">Open ↗</a>` : '—';
      }
      return fmt(it[col.key], col.key);
    });
    return { key: col.key, label: col.label, values };
  });

  rows.forEach(r=>{
    const plain = r.values.map(v => String(v).replace(/<[^>]*>/g,'').trim().toLowerCase());
    r.allSame = plain.every(x => x === plain[0]);
  });

  return rows;
}
function bestPerRow(row, items){
  const key = row.key;
  const N = items.length;
  const best = new Array(N).fill(false);
  const num = (x)=> Number.isFinite(Number(x)) ? Number(x) : null;

  if(key==='price_value'){
    let min = Infinity;
    items.forEach(it => { const v=num(it.price_value); if(v!=null && v<min) min=v; });
    items.forEach((it,i)=>{ const v=num(it.price_value); if(v!=null && v===min) best[i]=true; });
  } else if(key==='ram_gb' || key==='storage_ssd_gb' || key==='storage_hdd_gb'){
    let max = -Infinity;
    items.forEach(it => { const v=num(it[key]); if(v!=null && v>max) max=v; });
    items.forEach((it,i)=>{ const v=num(it[key]); if(v!=null && v===max) best[i]=true; });
  } else if(key==='gpu'){
    const anyDiscrete = items.some(it => hasDiscreteGPU(it.gpu));
    if(anyDiscrete){
      items.forEach((it,i)=>{ if(hasDiscreteGPU(it.gpu)) best[i]=true; });
    }
  } else if(key==='cpu'){
    const maxTier = Math.max(...items.map(it=>cpuTier(it.cpu)));
    items.forEach((it,i)=>{ if(cpuTier(it.cpu)===maxTier && maxTier>0) best[i]=true; });
  }
  return best;
}
function openCompareAll(){ openCompareItems(lastItems, 'Compare All (Specs)'); }
function openCompare(){
  const items = Array.from(shortlist.values());
  openCompareItems(items, 'Compare Shortlist (Specs)');
}
function openCompareItems(items, title){
  const wrap = document.getElementById('compareWrap');
  const titleEl = document.getElementById('compareTitle');
  titleEl.textContent = title || 'Compare';

  if(!items || !items.length){
    wrap.innerHTML = '<div class="sub">Nothing to compare.</div>';
    document.getElementById('overlay').classList.add('show');
    return;
  }

  const render = (diffOnly=false)=>{
    const rows = buildSpecRows(items);

    const headers = items.map((it,i)=>{
      const t = (it.title||'').trim();
      const short = t.length>28 ? t.slice(0,28)+'…' : t || `Item ${i+1}`;
      const price = Number.isFinite(Number(it.price_value)) ? `<div class="sub">${formatINR(Number(it.price_value))}</div>` : '';
      return `<th>${short}${price}</th>`;
    }).join('');

    const body = rows
      .filter(r => diffOnly ? !r.allSame : true)
      .map(r => {
        const bestMask = bestPerRow(r, items);
        const cells = r.values.map((v,idx)=>`<td class="${bestMask[idx] ? 'cell best' : 'cell'}">${v}</td>`).join('');
        const rowClass = (!diffOnly && r.allSame) ? 'dim' : '';
        return `<tr class="${rowClass}"><th class="cell spec-label">${r.label}</th>${cells}</tr>`;
      }).join('');

    wrap.innerHTML = `
      <div class="diff-toggle" style="margin:8px 0 12px;">
        <input type="checkbox" id="diffToggle" ${diffOnly?'checked':''} />
        <label for="diffToggle" class="sub">Show differences only</label>
      </div>
      <div class="table-wrap">
        <table class="comp-table">
          <thead><tr><th>Spec</th>${headers}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
    document.getElementById('diffToggle').onchange = (e)=> render(e.target.checked);
  };

  render(false);
  document.getElementById('overlay').classList.add('show');
}
function closeOverlay(){ document.getElementById('overlay').classList.remove('show'); }
function closeOverlayEvent(e){ if(e.target.id==='overlay') closeOverlay(); }

// ---------- Rendering ----------
function setLastUrl(artifacts){
  const el = document.getElementById('lastUrl');
  el.textContent = (artifacts && artifacts.last_url) ? `Last URL: ${artifacts.last_url}` : '';
}
function skeletonGrid(count=6){
  const gw = document.getElementById('gridWrap');
  gw.innerHTML = '';
  for(let i=0;i<count;i++){ const d=document.createElement('div'); d.className='skeleton'; gw.appendChild(d); }
}
function toggleView(){
  gridView = !document.getElementById('viewToggle').checked ? true : false;
  document.getElementById('gridWrap').classList.toggle('hidden', !gridView);
  document.getElementById('tableWrap').classList.toggle('hidden', gridView);
  document.getElementById('modeLabel').textContent = gridView ? 'Grid View' : 'Table View';
  render(lastItems);
}
function buildTableHeader(){
  const thead = document.getElementById('thead');
  thead.innerHTML = [
    '<tr>',
    '<th>#</th>',
    '<th>Title</th>',
    '<th>Price</th>',
    '<th>Why</th>',
    '<th>Pros</th>',
    '<th>Cons</th>',
    '<th>Brand</th>',
    '<th>CPU</th>',
    '<th>RAM</th>',
    '<th>SSD</th>',
    '<th>GPU</th>',
    '<th>OS</th>',
    '<th>Screen</th>',
    '<th>Source</th>',
    '<th>Open</th>',
    '<th>☆</th>',
    '</tr>'
  ].join('');
}
function renderGrid(items){
  const gw = document.getElementById('gridWrap');
  gw.innerHTML = '';
  if(!items.length){
    gw.innerHTML = '<div class="sub" style="padding:16px;">No results.</div>';
    return;
  }
  items.forEach(r=>{
    const priceText = r.price || (r.price_value ? formatINR(r.price_value) : 'N/A');
    const site = r.source || 'web';
    const card = document.createElement('div');
    card.className = 'card';
    const why = whyChoose(r);
    const pc = buildProsCons(r);
    card.innerHTML = `
      <div class="title">${r.title || '(no title)'}</div>
      ${why ? `<div class="why">Why this: ${why}</div>` : ''}
      ${badgeRow(r)}
      ${pc.pros.length ? `<div class="why"><strong>Pros:</strong> ${pc.pros.join(' • ')}</div>` : ''}
      ${pc.cons.length ? `<div class="why"><strong>Cons:</strong> ${pc.cons.join(' • ')}</div>` : ''}
      <div class="row">
        <div class="price">${priceText}</div>
        <span class="pill">${site}</span>
      </div>
      <div class="row" style="gap:8px;">
        <a class="chip" href="${r.link||'#'}" target="_blank" rel="noopener">Open ↗</a>
        <button class="chip" onclick='toggleSave(${JSON.stringify(r).replace(/\'/g,"&#39;")})'>☆ Save</button>
      </div>
    `;
    gw.appendChild(card);
  });
}
function renderTable(items){
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  if(!items.length){
    tb.innerHTML = '<tr><td colspan="16" class="sub" style="padding:12px">No results.</td></tr>';
    return;
  }
  buildTableHeader();
  items.forEach((r, idx)=>{
    const priceText = r.price || (r.price_value ? formatINR(r.price_value) : 'N/A');
    const site = r.source || 'web';
    const scr = Number(r.screen_inches);
    const why = whyChoose(r);
    const pc = buildProsCons(r);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="idx">${String(idx+1).padStart(2,'0')}</td>
      <td>${r.title || '(no title)'}${specBadgesAllowed()?('<div class="chips">'+badgeRow(r).replace('<div class="chips">','').replace('</div>','')+'</div>'):''}</td>
      <td>${priceText}</td>
      <td>${why || ''}</td>
      <td>${pc.pros.join(' • ')}</td>
      <td>${pc.cons.join(' • ')}</td>
      <td>${r.brand || ''}</td>
      <td>${r.cpu || ''}</td>
      <td>${toGB(r.ram_gb)? (toGB(r.ram_gb)+' GB') : ''}</td>
      <td>${r.storage_ssd_gb? (r.storage_ssd_gb+' GB') : ''}</td>
      <td>${r.gpu || ''}</td>
      <td>${r.os? String(r.os).toUpperCase() : ''}</td>
      <td>${Number.isFinite(scr) ? (scr+'"') : ''}</td>
      <td><span class="pill">${site}</span></td>
      <td class="idx"><a class="chip" href="${r.link||'#'}" target="_blank" rel="noopener">Open ↗</a></td>
      <td class="idx"><button class="chip" onclick='toggleSave(${JSON.stringify(r).replace(/\'/g,"&#39;")})'>☆</button></td>
    `;
    tb.appendChild(tr);
  });
}
function render(items){
  if(gridView) renderGrid(items); else renderTable(items);
  updateTray();
}

// ---------- History ----------
async function loadHistory(){
  const hist = document.getElementById('hist');
  hist.innerHTML = '<div class="sub">Loading…</div>';
  try{
    const res = await fetch('/history');
    const data = await res.json();
    if(!data.ok){ hist.innerHTML = '<div class="sub">Failed to load history.</div>'; return; }
    const items = data.items || [];
    if(!items.length){ hist.innerHTML = '<div class="sub">No history yet.</div>'; return; }
    hist.innerHTML = '';
    items.forEach((it, idx)=>{
      const d = new Date((it.ts||0)*1000);
      const when = d.toLocaleString();
      const div = document.createElement('div');
      div.className = 'item';
      div.innerHTML = `
        <div class="q">${it.query || ''}</div>
        <div class="meta">
          <span class="chip">site: ${it.site || 'auto'}</span>
          <span class="chip">count: ${it.count || 0}</span>
          ${it.max_price? `<span class="chip">max ₹${it.max_price}</span>`:''}
          ${it.min_price? `<span class="chip">min ₹${it.min_price}</span>`:''}
          <span class="chip">${when}</span>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap%;">
          <button class="btn ghost" onclick="reuseQuery(${idx})">Reuse</button>
          ${it.last_url? `<a class="chip" href="${it.last_url}" target="_blank" rel="noopener">Last URL</a>`:''}
        </div>
      `;
      div.dataset.idx = idx;
      hist.appendChild(div);
    });
    window.__HIST = items;
  }catch(e){
    console.error(e);
    hist.innerHTML = '<div class="sub">Error loading history.</div>';
  }
}
function reuseQuery(idx){
  const items = window.__HIST || [];
  const it = items[idx];
  if(!it) return;
  document.getElementById('q').value = it.query || '';
  const sel = document.getElementById('site');
  if(it.site && ['flipkart','amazon','reliance','croma'].includes(String(it.site).toLowerCase())){
    sel.value = it.site.toLowerCase();
  } else { sel.value = ''; }
  if(it.max_price) document.getElementById('budget').value = it.max_price;
  if(it.min_price) document.getElementById('min_price').value = it.min_price;
  syncURL();
  toast('Query loaded from history');
}

// ---------- URL share/sync ----------
function syncURL(){
  setParams({
    q: document.getElementById('q').value || '',
    site: document.getElementById('site').value || '',
    max: document.getElementById('max').value || '',
    budget: document.getElementById('budget').value || '',
    min: document.getElementById('min_price').value || ''
  });
}
function loadFromURL(){
  const qs = new URLSearchParams(location.search);
  const set = (id, v)=>{ if(v!==null && v!==undefined && v!=='') document.getElementById(id).value = v; };
  set('q', qs.get('q'));
  set('site', qs.get('site'));
  set('max', qs.get('max'));
  set('budget', qs.get('budget'));
  set('min_price', qs.get('min'));
}
function copyShareURL(){ syncURL(); copy(location.href); }

// ---------- Actions ----------
async function run(){
  const status = document.getElementById('status');
  status.textContent = 'running…';

  const q = document.getElementById('q').value;
  const site = document.getElementById('site').value || null;
  const max = Number(document.getElementById('max').value || 8);
  const budget = document.getElementById('budget').value ? Number(document.getElementById('budget').value) : null;
  const minp = document.getElementById('min_price').value ? Number(document.getElementById('min_price').value) : null;

  lastQuery = q;
  skeletonGrid();
  syncURL();

  const body = { query: q, max_results: max, site, budget, min_price: minp };

  try{
    const res = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const data = await res.json();
    if(!res.ok || !data.ok){
      status.textContent = 'error';
      toast('Run failed');
      document.getElementById('gridWrap').innerHTML = '<div class="sub" style="padding:16px;">Run failed.</div>';
      return;
    }
    lastItems = Array.isArray(data.results)? data.results : [];
    laptopMode = looksLikeLaptopQuery(lastQuery) || inferLaptopFromResults(lastItems);
    setLastUrl(data.artifacts || {});
    render(lastItems);
    status.textContent = 'done';
    loadHistory();
  }catch(e){
    console.error(e);
    status.textContent = 'error';
    toast('Unexpected error');
  }
}

async function exportFile(fmt){
  const status = document.getElementById('status');
  status.textContent = 'exporting…';

  const payload = {
    query: document.getElementById('q').value,
    max_results: Number(document.getElementById('max').value || 8),
    site: document.getElementById('site').value || null,
    budget: document.getElementById('budget').value ? Number(document.getElementById('budget').value) : null,
    min_price: document.getElementById('min_price').value ? Number(document.getElementById('min_price').value) : null,
    fmt
  };

  try{
    const res = await fetch('/export', {method:'POST',headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    if(!res.ok){ status.textContent='export failed'; toast('Export failed'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download = fmt==='csv'?'results.csv':'results.json';
    a.click(); URL.revokeObjectURL(url);
    status.textContent = 'downloaded';
  }catch(e){
    console.error(e);
    status.textContent = 'error';
  }
}

// ---------- Listeners & init ----------
['q','site','max','budget','min_price'].forEach(id=>{
  const el = document.getElementById(id)
  if (el && typeof el.addEventListener === 'function') {
    el.addEventListener('change', syncURL)
  }
});
document.getElementById('viewToggle').addEventListener('change', syncURL);
document.getElementById('overlay').addEventListener('click', closeOverlayEvent);

function init(){
  loadFromURL();
  toggleView(); // sets initial view & renders (empty)
  loadHistory();
}
init();
</script>
</body>
</html>
"""

@app.get("/")
def root():
    return {"message": "Web Navigator AI Agent is running. Open /demo for the modern UI (with Compare & Why). /history shows recent runs. /healthz & /readyz use a writable dir."}

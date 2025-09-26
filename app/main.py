from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from app.agent.planner import plan_from_query
from app.agent.executor import execute_plan
from app.utils.logger import logger
from app.utils.export import save_results  # CSV/JSON export

app = FastAPI(title="Web Navigator AI Agent", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# Silence favicon 404s
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

# ---------- /run API ----------

class RunRequest(BaseModel):
    query: str = Field(..., description="Natural language instruction from user")
    max_results: int = Field(5, ge=1, le=20)

class RunResponse(BaseModel):
    ok: bool
    query: str
    plan: Dict[str, Any]
    results: List[Dict[str, Any]]
    artifacts: Dict[str, Any]

@app.post("/run", response_model=RunResponse)
def run_agent(req: RunRequest):
    try:
        logger.info(f"Received query: {req.query}")
        plan = plan_from_query(req.query, req.max_results)
        logger.info(f"Generated plan: {plan}")
        results, artifacts = execute_plan(plan)
        return RunResponse(ok=True, query=req.query, plan=plan, results=results, artifacts=artifacts)
    except Exception as e:
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=str(e))

# ---------- /export API ----------

class ExportRequest(BaseModel):
    query: str
    max_results: int = 5
    site: Optional[str] = None        # "flipkart" | "amazon" | None (auto)
    budget: Optional[int] = None      # e.g., 50000
    fmt: str = "csv"                  # "csv" | "json"

@app.post("/export")
def export_run(req: ExportRequest):
    try:
        plan = plan_from_query(req.query, req.max_results)
        if req.site:
            plan["plan"][0]["site"] = req.site
        if req.budget is not None:
            plan["plan"][0]["max_price"] = req.budget
            q = plan["plan"][0].get("query") or "laptops"
            if "under" not in q.lower():
                plan["plan"][0]["query"] = f"{q} under {req.budget}"

        results, artifacts = execute_plan(plan)
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

# ---------- prettier /demo UI (TABLE VIEW) ----------

@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Web Navigator Demo — Table</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg:#0b0d13; --panel:#0f1422; --muted:#9aa3b2; --text:#e7ecf3; --border:#1f2a44;
      --brand1:#2563eb; --brand2:#7c3aed; --accent:#60a5fa; --ok:#10b981; --warn:#f59e0b;
    }
    *{box-sizing:border-box}
    body{margin:0;background:linear-gradient(180deg,#0b0d13,#0c1220);color:var(--text);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial}
    .container{max-width:1100px;margin:0 auto;padding:24px}
    h1{margin:0 0 16px;font-size:22px}
    .card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px;margin:12px 0;box-shadow:0 10px 30px rgba(0,0,0,.25)}
    label{font-size:12px;color:var(--muted);display:block;margin-bottom:6px}
    input,select,button{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#0c1220;color:var(--text)}
    button{cursor:pointer;border:0;background:linear-gradient(90deg,var(--brand1),var(--brand2));box-shadow:0 6px 14px rgba(124,58,237,.25)}
    button.secondary{background:transparent;border:1px solid var(--border)}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
    .controls{display:flex;flex-wrap:wrap;gap:10px}
    .status{padding:6px 10px;border-radius:999px;background:#0c1220;border:1px solid var(--border);color:var(--muted);font-size:12px}
    .bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin:10px 0}
    .right{display:flex;gap:10px;flex-wrap:wrap}
    .table-wrap{overflow:auto;border:1px solid var(--border);border-radius:12px}
    table{width:100%;border-collapse:separate;border-spacing:0;min-width:740px}
    thead th{position:sticky;top:0;background:#0b1324;border-bottom:1px solid var(--border);text-align:left;font-size:12px;color:var(--muted);padding:10px}
    tbody td{padding:12px 10px;border-bottom:1px solid var(--border)}
    tbody tr:hover{background:#0c162a}
    .idx{color:var(--muted);font-variant-numeric:tabular-nums}
    .title a{color:#dbe7ff;text-decoration:none}
    .title a:hover{text-decoration:underline}
    .pill{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;border:1px solid var(--border);background:#0c1220;color:#b8c3d6}
    .price{font-weight:700}
    .searchbox{max-width:280px}
    .hint{font-size:12px;color:var(--muted)}
    .nowrap{white-space:nowrap}
    .footer{display:flex;justify-content:flex-end;padding:10px 0;color:var(--muted);font-size:12px}
    @media (max-width:720px){ .grid3{grid-template-columns:1fr 1fr} }
  </style>
</head>
<body>
  <div class="container">
    <h1>Web Navigator AI Agent — Results (Table)</h1>

    <div class="card">
      <div class="grid2">
        <div>
          <label>Query</label>
          <input id="q" value="Find top 5 laptops under 50k on Flipkart">
        </div>
        <div>
          <label>Budget (INR, optional)</label>
          <input id="budget" type="number" placeholder="50000">
        </div>
      </div>

      <div class="grid3" style="margin-top:12px">
        <div>
          <label>Max results</label>
          <input id="max" type="number" value="5" min="1" max="20">
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
          <label>Sort (default)</label>
          <select id="sort">
            <option value="price_asc">Price ↑</option>
            <option value="price_desc">Price ↓</option>
            <option value="title_asc">Title A–Z</option>
            <option value="title_desc">Title Z–A</option>
          </select>
        </div>
      </div>

      <div class="bar">
        <div class="controls">
          <button onclick="run()">Run</button>
          <button class="secondary" onclick="exportFile('csv')">Export CSV</button>
          <button class="secondary" onclick="exportFile('json')">Export JSON</button>
          <span id="status" class="status">idle</span>
        </div>
        <div class="right">
          <div class="searchbox">
            <label>Filter in table</label>
            <input id="filter" placeholder="type to filter title/source…" oninput="applyFilter()">
          </div>
        </div>
      </div>

      <div class="table-wrap">
        <table id="resultTable">
          <thead>
            <tr>
              <th class="nowrap">#</th>
              <th class="nowrap sort" data-key="title">Title ▲▼</th>
              <th class="nowrap sort" data-key="price_value">Price ▲▼</th>
              <th class="nowrap">Source</th>
              <th class="nowrap">Open</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="footer" id="summary"></div>
      <div class="hint">Tip: Click a column with “▲▼” to sort. Use the filter box to narrow the table.</div>
    </div>
  </div>

<script>
let lastResults = [];
let currentSorted = [];
let currentSort = { key: 'price_value', dir: 'asc' };

function formatINR(n){
  if(n == null) return 'N/A';
  try { return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(n); }
  catch(e){ return '₹' + n; }
}

function dedupe(items){
  const seen = new Set(), out = [];
  for(const x of items){
    const key = (x.link||'') + '|' + (x.price_value ?? '');
    if(seen.has(key)) continue;
    seen.add(key); out.push(x);
  }
  return out;
}

function sortItems(items, key, dir){
  const arr = [...items];
  const norm = v => {
    if(key==='title') return (v?.title || '').toLowerCase();
    if(key==='price_value') return v?.price_value ?? 1e12;
    return v?.[key];
  };
  arr.sort((a,b)=>{
    const A = norm(a), B = norm(b);
    if(A==null && B==null) return 0;
    if(A==null) return 1;
    if(B==null) return -1;
    if(A<B) return dir==='asc'?-1:1;
    if(A>B) return dir==='asc'?1:-1;
    return 0;
  });
  return arr;
}

function renderTable(items){
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  if(!items.length){
    tb.innerHTML = '<tr><td colspan="5" class="hint">No results.</td></tr>';
    document.getElementById('summary').textContent = '';
    return;
  }
  items.forEach((r, idx)=>{
    const tr = document.createElement('tr');
    const priceText = r.price || (r.price_value ? formatINR(r.price_value) : 'N/A');
    const site = r.source || 'web';
    tr.innerHTML = `
      <td class="idx">${String(idx+1).padStart(2,'0')}</td>
      <td class="title"><a href="${r.link||'#'}" target="_blank" rel="noopener">${r.title || '(no title)'}</a></td>
      <td class="price">${priceText}</td>
      <td><span class="pill">${site}</span></td>
      <td class="nowrap"><a class="pill" href="${r.link||'#'}" target="_blank" rel="noopener">Open ↗</a></td>
    `;
    tb.appendChild(tr);
  });
  document.getElementById('summary').textContent = `${items.length} item(s)`;
}

function applyFilter(){
  const q = (document.getElementById('filter').value || '').toLowerCase().trim();
  if(!q){ renderTable(currentSorted); return; }
  const filtered = currentSorted.filter(r =>
    (r.title||'').toLowerCase().includes(q) ||
    (r.source||'').toLowerCase().includes(q)
  );
  renderTable(filtered);
}

async function run(){
  const body = {
    query: document.getElementById('q').value,
    max_results: Number(document.getElementById('max').value||5)
  };
  const site = document.getElementById('site').value;
  const budget = document.getElementById('budget').value;
  if(site) body.query += ' on ' + site;
  if(budget) body.query += ' under ' + budget;

  const status = document.getElementById('status');
  status.textContent = 'running…';
  try{
    const res = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if(!res.ok){ status.textContent='failed '+res.status; return; }
    const data = await res.json();
    lastResults = dedupe(data.results || []);
    // default sort from dropdown
    const sel = document.getElementById('sort').value;
    if(sel.startsWith('price')) currentSort.key = 'price_value';
    else currentSort.key = 'title';
    currentSort.dir = sel.endsWith('desc') ? 'desc' : 'asc';

    currentSorted = sortItems(lastResults, currentSort.key, currentSort.dir);
    renderTable(currentSorted);
    document.getElementById('filter').value = '';
    status.textContent = 'done';
  }catch(e){
    console.error(e);
    status.textContent = 'error';
  }
}

async function exportFile(fmt){
  const payload = {
    query: document.getElementById('q').value,
    max_results: Number(document.getElementById('max').value||5),
    site: document.getElementById('site').value || null,
    budget: document.getElementById('budget').value ? Number(document.getElementById('budget').value) : null,
    fmt
  };
  const status = document.getElementById('status');
  status.textContent = 'exporting…';
  const res = await fetch('/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!res.ok){ status.textContent='export failed'; return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href=url; a.download = fmt==='csv'?'results.csv':'results.json';
  a.click(); URL.revokeObjectURL(url);
  status.textContent = 'downloaded';
}

// click-to-sort on header
document.addEventListener('click', (e)=>{
  const th = e.target.closest('th.sort');
  if(!th) return;
  const key = th.dataset.key;
  if(currentSort.key === key){
    currentSort.dir = (currentSort.dir === 'asc') ? 'desc' : 'asc';
  } else {
    currentSort.key = key;
    currentSort.dir = 'asc';
  }
  currentSorted = sortItems(currentSorted.length ? currentSorted : lastResults, currentSort.key, currentSort.dir);
  applyFilter(); // keeps filter active
});
</script>
</body>
</html>
"""

@app.get("/")
def root():
    return {"message": "Web Navigator AI Agent is running. Use POST /run {query, max_results} or POST /export to download CSV/JSON"}

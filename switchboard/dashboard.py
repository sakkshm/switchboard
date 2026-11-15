"""Local dashboard: live graph counts plus evals/results JSON on one page."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, planner
from .hydra import Hydra

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"


def load_json(name):
    """Read evals/results/<name>, or {} when the eval has not run."""
    try:
        return json.loads((RESULTS / name).read_text())
    except (OSError, ValueError):
        return {}


def snapshot():
    """Live counts; works even when HydraDB is down (ready=False)."""
    current = config.load()
    hydra = Hydra.from_config(current)
    try:
        tools = hydra.scalar("MATCH (t:Tool) RETURN count(*) AS n")
        edges = hydra.scalar("MATCH ()-[r:FEEDS]->() RETURN count(*) AS n")
        plans = hydra.scalar("MATCH (p:Plan) RETURN count(*) AS n")
        indexed = planner.load_tools(hydra)
    except Exception as exc:
        return {"ready": False, "url": hydra.url, "error": " ".join(str(exc).split())[:200]}
    by_server = {}
    for tool in indexed:
        by_server[tool["server"]] = by_server.get(tool["server"], 0) + 1
    return {
        "ready": True,
        "url": hydra.url,
        "servers": len(current["servers"]),
        "tools": tools,
        "edges": edges,
        "plans": plans,
        "by_server": by_server,
    }


def graph_data():
    """Nodes and FEEDS edges for the list and the SVG map."""
    hydra = Hydra.from_config(config.load())
    try:
        tools = planner.load_tools(hydra)
        rows = hydra.query("MATCH (a:Tool)-[r:FEEDS]->(b:Tool) RETURN a.key, b.key, r.weight")
    except Exception as exc:
        return {"ready": False, "error": " ".join(str(exc).split())[:200]}
    return {
        "ready": True,
        "nodes": [
            {
                "key": tool["key"],
                "server": tool["server"],
                "consumes": tool.get("consumes") or [],
                "produces": tool.get("produces") or [],
            }
            for tool in sorted(tools, key=lambda item: item["key"])
        ],
        "edges": [
            {"source": source, "target": target, "weight": 1.0 if weight is None else weight}
            for source, target, weight in rows
        ],
    }


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Switchboard dashboard</title>
<style>
:root{color-scheme:dark;--paper:#f1f0e9;--ink:#0a0a0a;--orange:#ff6333;
--mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
--sans:"Inter","Helvetica Neue","Segoe UI",Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--paper);font-family:var(--sans)}
.site{max-width:1100px;margin:auto;padding:0 36px}
.header{height:80px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #30302e}
.brand{font-size:22px;font-weight:600;letter-spacing:-1.1px}
.header nav{font:11px var(--mono);color:#b2b2ab}
.eyebrow{font:10px var(--mono);letter-spacing:1.5px;color:#b8b8b0;margin:40px 0 0}
.eyebrow i{display:inline-block;width:6px;height:6px;background:var(--orange);margin-right:10px;font-style:normal}
h1{font-size:clamp(40px,5vw,64px);font-weight:500;letter-spacing:-.055em;line-height:1.04;margin:20px 0 12px}
h1 span{color:#8e8e86}
.sub{font-size:14px;line-height:1.8;color:#aaa99f;max-width:640px;margin:0}
.kicker{font:10px var(--mono);letter-spacing:1px;color:var(--orange);margin:40px 0 12px}
.panel{background:#10100f;border:1px solid #30302c;padding:20px;margin:0 0 8px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:0}
.card{border:1px solid #33332e;background:#141412;padding:12px 16px;min-width:120px}
.card span{font:9px var(--mono);letter-spacing:.6px;color:#77776c}
.card b{font-size:24px;display:block;margin-top:4px}
.card.ok b{color:var(--paper)}.card.bad b{color:var(--orange)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{font:9px var(--mono);letter-spacing:.6px;color:#77776c;text-align:right;padding:8px;border-bottom:1px solid #30302c}
td{padding:10px 8px;border-bottom:1px solid #22221f;text-align:right;font-family:var(--mono);font-size:12px;color:#c5c5b9}
th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:0}
.muted{color:#96968b;font-size:11px;font-family:var(--mono)}
.bar{height:6px;background:#22221f;margin-top:6px}
.bar i{display:block;height:100%;background:var(--orange)}
.gwrap{display:grid;grid-template-columns:1.2fr 1fr;gap:16px}
#map{width:100%;height:auto;background:#0a0a0a;border:1px solid #22221f}
#map .edge{fill:none;stroke:#404038;stroke-width:1}
#map .edge.hot{stroke:var(--orange)}
#map .node{fill:#f1f0e9;stroke:none}
#map g[data-k]{cursor:pointer}
#map g[data-k]:hover .node{fill:var(--orange)}
#map text{fill:#c5c5b9;font:10px var(--mono)}
#map #tip-text{fill:#0a0a0a;font:11px var(--mono)}}
#map .srv{fill:#77776c;font:9px var(--mono)}
.tools{max-height:420px;overflow:auto}
.tools input{width:100%;background:#080808;border:1px solid #343430;color:#d7d7cb;font:11px var(--mono);padding:8px;margin-bottom:8px}
.mwrap{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.mwrap .panel{margin:0}
.chart{width:100%;height:auto;background:#0a0a0a;border:1px solid #22221f}
.chart .axis{stroke:#30302c;stroke-width:1}
.chart .grid{stroke:#1c1c1a;stroke-width:1}
.chart text{fill:#77776c;font:9px var(--mono)}
.legend{display:flex;gap:16px;flex-wrap:wrap;font:10px var(--mono);color:#aaa99f;margin:8px 0 0}
.legend i{display:inline-block;width:10px;height:3px;margin-right:6px;vertical-align:middle}
@media(max-width:900px){.gwrap,.mwrap{grid-template-columns:1fr}}
a{color:inherit}
</style></head><body><div class="site">
<header class="header"><span class="brand">switchboard</span><nav>DASHBOARD</nav></header>
<div class="kicker">01 / LIVE GRAPH</div>
<div class="panel"><div class="cards" id="status"></div><p class="muted" id="servers"></p></div>
<div class="kicker">02 / GRAPH</div>
<div class="panel"><div class="gwrap">
<div><svg id="map" role="img" aria-label="Tool dependency graph"></svg><p class="muted" id="gmeta"></p></div>
<div class="tools"><input id="q" placeholder="filter tools"/><table id="tools"><tr><th>tool</th><th>consumes</th><th>produces</th></tr></table></div>
</div></div>
<div class="kicker">03 / METRICS</div>
<div class="mwrap">
<div class="panel"><p class="muted">success / order / top1 by round</p><svg class="chart" id="c-learn" viewBox="0 0 400 180" role="img" aria-label="Learning curve chart"></svg><p class="legend"><span><i style="background:#ff6333"></i>success</span><span><i style="background:#f1f0e9"></i>order</span><span><i style="background:#77776c"></i>top1</span></p></div>
<div class="panel"><p class="muted">latency median / p95 ms by round</p><svg class="chart" id="c-lat" viewBox="0 0 400 180" role="img" aria-label="Latency chart"></svg><p class="legend"><span><i style="background:#ff6333"></i>p95</span><span><i style="background:#f1f0e9"></i>median</span></p></div>
</div>
<div class="panel" style="margin-top:16px"><p class="muted">tokens in context: native catalog vs gateway</p><svg class="chart" id="c-tok" viewBox="0 0 400 70" role="img" aria-label="Token comparison"></svg><p class="muted" id="tokens"></p></div>
<div class="kicker">04 / LEARNED CURVE (HOLDOUT)</div>
<div class="panel"><table id="curve"><tr><th>round</th><th>success</th><th>order</th><th>top1</th><th>p95 ms</th><th>failed</th></tr></table></div>
<div class="kicker">05 / BASELINES</div>
<div class="panel"><table id="base"><tr><th>mode</th><th>success</th><th>order</th><th>top1</th><th>p95 ms</th></tr></table></div>
<script>
const f = (u) => fetch(u).then((r) => r.json());
// shortcut: hand-rolled SVG (no chart lib) - 46 nodes fit a grid; upgrade to a viz lib past ~500 nodes.
f("/api/status").then((s) => {
  const cards = [["ready", s.ready === true ? "yes" : "no", s.ready === true],
    ["servers", s.servers ?? "-", 1], ["tools", s.tools ?? "-", 1],
    ["edges", s.edges ?? "-", 1], ["plans", s.plans ?? "-", 1]];
  document.getElementById("status").innerHTML = cards.map(([k, v, ok]) =>
    `<div class="card ${ok ? "ok" : v === "no" ? "bad" : "ok"}"><span>${k}</span><b>${v}</b></div>`).join("");
  document.getElementById("servers").textContent = s.error ? "error: " + s.error
    : (s.by_server ? Object.entries(s.by_server).map(([k, v]) => `${k}: ${v}`).join(" / ") + " · " + s.url : s.url || "");
});
let ALL_NODES = [];
function drawMap(nodes, edges) {
  const svg = document.getElementById("map");
  const servers = [...new Set(nodes.map((n) => n.server))].sort();
  const cols = servers.reduce((m, s) => (m[s] = servers.indexOf(s), m), {});
  const perServer = {};
  const pos = {};
  nodes.forEach((n) => {
    const i = (perServer[n.server] = (perServer[n.server] || 0) + 1);
    pos[n.key] = {x: 24 + cols[n.server] * 210, y: 26 + i * 22};
  });
  const W = 20 + servers.length * 210, H = 26 + Math.max(...Object.values(perServer), 1) * 22 + 20;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = servers.map((s, i) => `<text class="srv" x="${24 + i * 210}" y="14">${s}</text>`).join("")
    + edges.filter((e) => pos[e.source] && pos[e.target]).map((e) => {
      const a = pos[e.source], b = pos[e.target];
      return `<path class="edge" data-s="${e.source}" data-t="${e.target}" d="M${a.x} ${a.y} C ${a.x + 60} ${a.y}, ${b.x - 60} ${b.y}, ${b.x} ${b.y}"><title>${e.source} -&gt; ${e.target} (${e.weight})</title></path>`;
    }).join("")
    + nodes.map((n) => {
      const p = pos[n.key];
      return `<g data-k="${n.key}"><circle class="node" cx="${p.x}" cy="${p.y}" r="4"/><title>${n.key}&#10;consumes: ${(n.consumes || []).join(", ")}&#10;produces: ${(n.produces || []).join(", ")}</title></g>`;
    }).join("")
    + `<g id="tip" visibility="hidden"><rect id="tip-box" fill="#f1f0e9"/><text id="tip-text" x="0" y="0"></text></g>`;
  document.getElementById("gmeta").textContent = `${nodes.length} tools · ${edges.length} FEEDS edges`;
  const tip = svg.querySelector("#tip"), tipBox = svg.querySelector("#tip-box"), tipText = svg.querySelector("#tip-text");
  const showTip = (g) => {
    const k = g.getAttribute("data-k"), p = pos[k];
    tipText.textContent = k;
    const w = 16 + k.length * 6.2;
    tipBox.setAttribute("x", p.x + 10);
    tipBox.setAttribute("y", p.y - 26);
    tipBox.setAttribute("width", w);
    tipBox.setAttribute("height", 20);
    tipText.setAttribute("x", p.x + 18);
    tipText.setAttribute("y", p.y - 12);
    tipText.setAttribute("fill", "#0a0a0a");
    tip.setAttribute("visibility", "visible");
    svg.querySelectorAll(".edge").forEach((e) => e.classList.toggle("hot",
      e.getAttribute("data-s") === k || e.getAttribute("data-t") === k));
  };
  const hideTip = () => {
    tip.setAttribute("visibility", "hidden");
    svg.querySelectorAll(".edge").forEach((e) => e.classList.remove("hot"));
  };
  svg.querySelectorAll("g[data-k]").forEach((g) => {
    g.addEventListener("mouseenter", () => showTip(g));
    g.addEventListener("mouseleave", hideTip);
  });
}
function renderTools(filter) {
  const q = (filter || "").toLowerCase();
  const rows = ALL_NODES.filter((n) => !q || n.key.toLowerCase().includes(q)).slice(0, 200);
  document.getElementById("tools").innerHTML =
    `<tr><th>tool</th><th>consumes</th><th>produces</th></tr>` + rows.map((n) =>
    `<tr><td>${n.key}</td><td>${(n.consumes || []).join(", ")}</td><td>${(n.produces || []).join(", ")}</td></tr>`).join("");
}
f("/api/graph").then((g) => {
  if (!g.ready) { document.getElementById("gmeta").textContent = "error: " + (g.error || "graph down"); return; }
  ALL_NODES = g.nodes;
  drawMap(g.nodes, g.edges);
  renderTools("");
});
document.getElementById("q").addEventListener("input", (e) => renderTools(e.target.value));
function line(svg, series, max) {
  const W = 400, H = 180, P = 28;
  const n = Math.max(...series.map((s) => s.values.length));
  const X = (i) => P + (n <= 1 ? 0 : i * (W - 2 * P) / (n - 1));
  const Y = (v) => H - P - (v / max) * (H - 2 * P);
  let html = [0.25, 0.5, 0.75].map((g) =>
    `<line class="grid" x1="${P}" y1="${Y(max * g)}" x2="${W - 8}" y2="${Y(max * g)}"/>`).join("");
  series.forEach((s) => {
    html += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="` +
      s.values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ") + `"/>`;
  });
  html += `<line class="axis" x1="${P}" y1="${P}" x2="${P}" y2="${H - P}"/><line class="axis" x1="${P}" y1="${H - P}" x2="${W - 8}" y2="${H - P}"/>`;
  svg.innerHTML = html;
}
f("/api/results").then((r) => {
  if (!r.learned) return;
  document.getElementById("curve").innerHTML += r.learned.map((row) =>
    `<tr><td>round ${row.round}<div class="bar"><i style="width:${Math.round(row.task_success * 100)}%"></i></div></td><td>${row.task_success.toFixed(2)}</td><td>${row.order_validity.toFixed(2)}</td><td>${row.top1.toFixed(2)}</td><td>${row.latency_p95_ms.toFixed(0)}</td><td>${(row.failures || []).join(", ")}</td></tr>`).join("");
  const rows = [["bm25", r.bm25], ["bm25_raw", r.bm25_raw],
    ["uniform/holdout", r.uniform && r.uniform.holdout], ["uniform/train", r.uniform && r.uniform.train]];
  document.getElementById("base").innerHTML += rows.filter(([, v]) => v).map(([k, v]) =>
    `<tr><td>${k}</td><td>${v.task_success.toFixed(2)}</td><td>${v.order_validity.toFixed(2)}</td><td>${v.top1.toFixed(2)}</td><td>${v.latency_p95_ms.toFixed(0)}</td></tr>`).join("");
  line(document.getElementById("c-learn"), [
    {color: "#ff6333", values: r.learned.map((d) => d.task_success)},
    {color: "#f1f0e9", values: r.learned.map((d) => d.order_validity)},
    {color: "#77776c", values: r.learned.map((d) => d.top1)},
  ], 1);
  line(document.getElementById("c-lat"), [
    {color: "#ff6333", values: r.learned.map((d) => d.latency_p95_ms)},
    {color: "#f1f0e9", values: r.learned.map((d) => d.latency_median_ms)},
  ], Math.max(...r.learned.map((d) => d.latency_p95_ms), 1));
});
f("/api/tokens").then((t) => {
  if (!t.gateway) return;
  const svg = document.getElementById("c-tok");
  const max = Math.max(t.native_total.tokens, t.gateway.tokens);
  const w = (v) => Math.max(2, Math.round(v / max * 330));
  svg.innerHTML = `<text x="0" y="20">native</text><rect x="60" y="8" width="${w(t.native_total.tokens)}" height="12" fill="#77776c"/>`
    + `<text x="0" y="52">gateway</text><rect x="60" y="40" width="${w(t.gateway.tokens)}" height="12" fill="#ff6333"/>`;
  document.getElementById("tokens").textContent =
    `native ${t.native_total.tools} tools / ${t.native_total.tokens} tokens vs gateway ${t.gateway.tools} tools / ${t.gateway.tokens} tokens`;
});
</script></div></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, content_type):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._send(json.dumps(snapshot()), "application/json")
        elif self.path == "/api/graph":
            self._send(json.dumps(graph_data()), "application/json")
        elif self.path == "/api/results":
            self._send(json.dumps(load_json("latest.json")), "application/json")
        elif self.path == "/api/tokens":
            self._send(json.dumps(load_json("tokens.json")), "application/json")
        else:
            self.send_response(404)
            self.end_headers()


def serve(host="127.0.0.1", port=8000):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"dashboard http://{host}:{server.server_port}/")
    server.serve_forever()


def dashboard_command(args):
    host, _, port = (args.http or "127.0.0.1:8000").rpartition(":")
    serve(host or "127.0.0.1", int(port or 8000))
    return 0

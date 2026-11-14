"use strict";

const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
let savedBaselineOrder = null;
const fmt = (value) => typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value) : "—";
const percent = (value) => typeof value === "number" && Number.isFinite(value) ? `${fmt(value * 100)}%` : "—";
const elapsed = (value) => typeof value === "number" && Number.isFinite(value) ? value < 1000 ? `${fmt(value)} ms` : `${fmt(value / 1000)} s` : "—";
const setText = (id, value) => { if ($(id)) $(id).textContent = value; };
const node = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};

async function api(path, payload) {
  const options = { headers: { Accept: "application/json" } };
  if (payload !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); }
  catch (_) { throw new Error(`The server returned an unreadable response (HTTP ${response.status}).`); }
  if (!response.ok) {
    const detail = data.detail ?? data.error ?? data;
    const message = typeof detail === "string" ? detail : detail.message ?? detail.error ?? JSON.stringify(detail);
    const error = new Error(message || `Request failed (HTTP ${response.status}).`);
    error.data = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

function showError(id, error) {
  const box = $(id);
  if (!box) return;
  box.textContent = error instanceof TypeError ? "Could not reach Switchboard. Check that the app is running, then try again." : error.message || String(error);
  box.hidden = false;
}

async function health() {
  try {
    await api("/readyz");
    if ($("engine")) $("engine").dataset.status = "ok";
  } catch (_) {
    if ($("engine")) $("engine").dataset.status = "error";
  }
}

const presets = {
  issue: {
    task: "comment on the failing test issue in acme/api",
    inputs: { repo: "acme/api", message: "Investigating the failing test." }
  },
  review: {
    task: "post a code review for the authentication diff",
    inputs: { path: "src/auth.py", message: "Reviewed the authentication change." }
  },
  incident: {
    task: "notify the incident channel about the checkout error",
    inputs: { service: "checkout", channel: "incidents", message: "Investigating checkout errors." }
  }
};

function statusLabel(value) {
  return ({ success: "Completed", ok: "Completed", completed: "Completed", blocked: "Permission refused", failed: "Failed", error: "Failed", timeout: "Timed out", skipped: "Skipped", pending: "Waiting", running: "Running" })[value] || value || "Ready";
}

function renderChain(plan, trace = {}) {
  const list = $("chain");
  if (!list) return;
  list.replaceChildren();
  const steps = plan?.steps || trace.steps || [];
  if (!steps.length) {
    list.append(node("div", "empty-chain", "No executable path was returned for this task."));
    setText("chain-meta", "NO PLAN");
    return;
  }
  const traceSteps = trace.steps || [];
  let stopped = false;
  steps.forEach((step, index) => {
    const executed = traceSteps[index];
    const state = executed?.status || (stopped ? "skipped" : "pending");
    if (["blocked", "error", "failed", "timeout"].includes(state)) stopped = true;
    const item = node("div", "chain-step");
    item.dataset.state = state;
    const header = node("div", "step-top");
    header.append(node("span", "", String(index + 1).padStart(2, "0")), node("span", "", statusLabel(state)));
    item.append(header, node("div", "step-name", step.tool || step.key || "Unknown tool"));
    list.append(item);
  });
  setText("chain-meta", `${steps.length} STEPS · ORDERED BY THE GRAPH`);
}

function renderTrace(trace) {
  const body = $("trace-body");
  if (!body) return;
  body.replaceChildren();
  const status = trace.status || "pending";
  const statusElement = $("trace-status");
  if (statusElement) {
    statusElement.dataset.state = status;
    statusElement.textContent = statusLabel(status);
  }
  setText("trace-id", trace.id ? `trace / ${trace.id}` : "switchboard / no trace recorded");
  if ($("trace-link") && trace.id) {
    $("trace-link").href = `${page === "trace" ? "/trace" : "/traces"}/${encodeURIComponent(trace.id)}`;
    $("trace-link").textContent = page === "trace" ? "View trace JSON ↗" : "Open full trace ↗";
    $("trace-link").hidden = false;
  }
  const list = node("div", "trace-list");
  (trace.steps || []).forEach((step, index) => {
    const detail = node("details", "trace-item");
    const state = step.status || "pending";
    detail.dataset.state = state;
    if (["blocked", "failed", "error", "timeout"].includes(state)) detail.open = true;
    const summary = node("summary");
    summary.append(node("span", "", `${String(index + 1).padStart(2, "0")}  ${step.tool || step.key || "Unknown tool"}`), node("span", "trace-state", statusLabel(state)), node("span", "trace-time", elapsed(step.wall_ms ?? step.duration_ms ?? step.ms)));
    const value = {};
    if (step.args !== undefined) value.arguments = step.args;
    if (step.result !== undefined) value.result = step.result;
    if (step.error !== undefined) value.error = step.error;
    if (step.reason !== undefined) value.reason = step.reason;
    detail.append(summary, node("pre", "", JSON.stringify(value, null, 2)));
    list.append(detail);
  });
  if (trace.error) list.append(node("div", "trace-error", typeof trace.error === "string" ? trace.error : trace.error.message || JSON.stringify(trace.error)));
  if (!list.childElementCount) body.append(node("div", "console-empty", "No tool calls were recorded. Discovery or validation may have refused the request before execution."));
  else body.append(list);
}

function renderComparison(data) {
  const baseline = data.baseline || {};
  const result = data.switchboard || {};
  const plan = data.plan || {};
  const trace = data.trace || {};
  setText("baseline-tools", fmt(baseline.tools));
  setText("baseline-tokens", fmt(baseline.tokens));
  setText("baseline-graph", fmt(baseline.graph_queries ?? 0));
  const baselineValidity = baseline.order_validity ?? savedBaselineOrder;
  setText("baseline-validity", typeof baselineValidity === "number" ? percent(baselineValidity) : "Not measured");
  setText("selected-tools", fmt(result.tools ?? plan.steps?.length));
  setText("selected-tokens", fmt(result.tokens));
  setText("graph-queries", fmt(result.graph_queries ?? plan.graph_queries));
  setText("tool-calls", fmt(result.tool_calls ?? trace.metrics?.tool_calls));
  setText("wall-time", elapsed(result.wall_ms ?? trace.metrics?.wall_ms));
  setText("plan-kind", result.reused || plan.reused ? "PLAN REUSED" : "GRAPH TRAVERSED");
  setText("selected-tool-names", (plan.steps || []).map((step) => step.tool).join(" · ") || "No tools selected.");
  const isSuccess = ["success", "ok", "completed"].includes(trace.status);
  const savedTokens = typeof baseline.tokens === "number" && typeof result.tokens === "number" && baseline.tokens > 0 ? (baseline.tokens - result.tokens) / baseline.tokens : null;
  let note = isSuccess ? "Execution completed. Every tool call passed its permission check." : "Execution stopped. Open the trace below to inspect the decision.";
  if (savedTokens !== null && savedTokens >= 0) note = `${percent(savedTokens)} fewer context tokens. ${note}`;
  setText("result-note", note);
  renderChain(plan, trace);
  renderTrace(trace);
}

async function setupCompare() {
  let busy = false;
  let lastPayload = null;
  let lastPlanId = null;
  function changed() {
    lastPayload = null;
    lastPlanId = null;
    $("repeat").disabled = true;
    document.querySelectorAll("[data-preset]").forEach((button) => button.setAttribute("aria-pressed", "false"));
  }
  $("task").addEventListener("input", changed);
  $("inputs").addEventListener("input", changed);
  document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => {
    const preset = presets[button.dataset.preset];
    if (!preset || busy) return;
    $("task").value = preset.task;
    $("inputs").value = JSON.stringify(preset.inputs, null, 2);
    changed();
    button.setAttribute("aria-pressed", "true");
    $("error").hidden = true;
  }));

  async function run(repeat = false) {
    if (busy) return;
    $("error").hidden = true;
    let payload;
    try {
      if (repeat && lastPayload) payload = { ...lastPayload };
      else {
        const task = $("task").value.trim();
        if (!task) throw new Error("Enter a task before running the comparison.");
        let inputs;
        try { inputs = JSON.parse($("inputs").value); }
        catch (_) { throw new Error("Input arguments must be valid JSON. Open Input arguments to correct them."); }
        if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) throw new Error("Input arguments must be a JSON object, for example {\"repo\": \"acme/api\"}.");
        payload = { task, inputs };
      }
      payload.principal = $("readonly").checked ? "readonly" : "demo";
      // Re-executing a known plan demonstrates the executor's independent
      // grant check. Fresh read-only requests still use discovery filtering.
      if (payload.principal === "readonly" && lastPlanId) payload.plan_id = lastPlanId;
      else delete payload.plan_id;
    } catch (error) { showError("error", error); return; }
    busy = true;
    document.body.classList.add("busy");
    ["run", "repeat", "task", "inputs", "readonly"].forEach((id) => { $(id).disabled = true; });
    document.querySelectorAll("[data-preset]").forEach((button) => { button.disabled = true; });
    setText("run-label", "Executing…");
    setText("trace-status", "Planning and executing…");
    $("trace-status").dataset.state = "running";
    try {
      const result = await api("/compare", payload);
      renderComparison(result);
      lastPayload = payload;
      lastPlanId = result.plan?.key || null;
      if (result.error) showError("error", typeof result.error === "string" ? new Error(result.error) : result.error);
    } catch (error) {
      showError("error", error);
      setText("trace-status", "Request failed");
      $("trace-status").dataset.state = "error";
      const data = error.data?.detail?.trace ? error.data.detail : error.data;
      if (data?.trace) { renderTrace(data.trace); renderChain(data.plan, data.trace); }
      else {
        renderChain({ steps: [] });
        renderTrace({ status: "error", steps: [], error: error.message });
        ["selected-tools", "selected-tokens", "graph-queries", "tool-calls", "wall-time"].forEach((id) => setText(id, "—"));
        setText("plan-kind", "NO EXECUTABLE PLAN");
        setText("selected-tool-names", "No tools selected.");
        setText("result-note", "The request could not produce an executable plan. See the error and trace below.");
      }
    } finally {
      busy = false;
      document.body.classList.remove("busy");
      ["run", "task", "inputs", "readonly"].forEach((id) => { $(id).disabled = false; });
      $("repeat").disabled = !lastPayload;
      document.querySelectorAll("[data-preset]").forEach((button) => { button.disabled = false; });
      setText("run-label", "Run comparison");
    }
  }
  $("task-form").addEventListener("submit", (event) => { event.preventDefault(); run(); });
  $("repeat").addEventListener("click", () => run(true));
  try {
    const result = await api("/api/catalog");
    const tools = result.tools || [];
    setText("baseline-tools", fmt(tools.length));
    setText("all-tool-names", tools.map((tool) => tool.key || tool.name).join(" · "));
  } catch (_) { setText("all-tool-names", "Catalog unavailable. Check the app and graph connection."); }
  try {
    const data = await api("/api/evals");
    const measured = (data.results?.measurements || []).filter((row) => row.variant === "bm25" && typeof row.order_validity === "number");
    if (measured.length) {
      savedBaselineOrder = measured.reduce((total, row) => total + row.order_validity, 0) / measured.length;
      setText("baseline-validity", percent(savedBaselineOrder));
    }
  } catch (_) { /* Evaluations are optional; never fill missing measurements. */ }
}

async function setupCatalog() {
  try {
    const data = await api("/api/catalog");
    const tools = data.tools || [];
    const usage = data.plan_usage || {};
    function render() {
      const query = $("catalog-search").value.trim().toLowerCase();
      const matches = tools.filter((tool) => [tool.key, tool.name, tool.description, tool.server, tool.risk, ...(tool.consumes || []), ...(tool.produces || [])].join(" ").toLowerCase().includes(query));
      const grid = $("catalog-grid");
      grid.replaceChildren();
      matches.forEach((tool) => {
        const card = node("article", "tool-card");
        const top = node("div", "tool-card-top");
        const risk = node("span", "risk", tool.risk || (tool.writes ? "write" : "read"));
        risk.dataset.risk = tool.risk || (tool.writes ? "write" : "read");
        top.append(node("h2", "", tool.key || tool.name), risk);
        const description = (tool.description || tool.desc || "").replace(/\s*\[consumes:[^\]]*\]\s*$/, "");
        card.append(top, node("p", "tool-description", description));
        for (const [label, values] of [["Consumes", tool.consumes], ["Produces", tool.produces]]) {
          const cap = node("div", "capability");
          cap.append(node("span", "", label), document.createTextNode(values?.length ? values.join(", ") : label === "Consumes" ? "Entry point" : "—"));
          card.append(cap);
        }
        const footer = node("div", "tool-card-footer");
        const key = tool.key || tool.name;
        footer.append(node("span", "", tool.server || "Unknown server"), node("span", "", `${fmt(usage[key])} saved plans`));
        const schema = node("details", "schema");
        schema.append(node("summary", "", "Input schema"), node("pre", "", JSON.stringify(tool.inputSchema || tool.input_schema || {}, null, 2)));
        card.append(footer, schema);
        grid.append(card);
      });
      setText("catalog-count", `${matches.length} of ${tools.length} tools`);
      $("catalog-empty").hidden = matches.length > 0;
    }
    $("catalog-search").addEventListener("input", render);
    render();
  } catch (error) {
    setText("catalog-count", "Catalog unavailable");
    showError("catalog-error", error);
  }
}

function svgNode(name, attributes, text) {
  const item = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes || {}).forEach(([key, value]) => item.setAttribute(key, String(value)));
  if (text !== undefined) item.textContent = text;
  return item;
}

function renderLearning(rows) {
  if (!rows.length) return;
  $("learning-section").hidden = false;
  const table = $("learning-table");
  table.replaceChildren();
  rows.forEach((row) => {
    const tr = node("tr");
    [row.round, percent(row.top1_accuracy), percent(row.task_success)].forEach((value) => tr.append(node("td", "", String(value ?? "—"))));
    table.append(tr);
  });
  const chart = $("learning-chart");
  chart.replaceChildren();
  const width = 900, height = 225, left = 44, right = 24, top = 15, bottom = 35;
  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Held-out top-1 plan accuracy and task success by training round. Exact values are in the table below." });
  const x = (index) => left + index * (width - left - right) / Math.max(rows.length - 1, 1);
  const y = (value) => height - bottom - value * (height - top - bottom);
  [0, .5, 1].forEach((value) => {
    svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: "#d9dfd3", "stroke-width": 1 }), svgNode("text", { x: left - 10, y: y(value) + 4, fill: "#62716b", "font-size": 10, "text-anchor": "end", "font-family": "monospace" }, percent(value)));
  });
  rows.forEach((row, i) => svg.append(svgNode("text", { x: x(i), y: height - 8, fill: "#62716b", "font-size": 10, "text-anchor": "middle", "font-family": "monospace" }, `Round ${row.round}`)));
  for (const [field, color] of [["top1_accuracy", "#087c63"], ["task_success", "#ad873b"]]) {
    let segment = [];
    const flush = () => {
      if (segment.length) svg.append(svgNode("polyline", { points: segment.join(" "), fill: "none", stroke: color, "stroke-width": 2.5 }));
      segment = [];
    };
    rows.forEach((row, i) => {
      const value = row[field];
      if (typeof value !== "number" || !Number.isFinite(value)) { flush(); return; }
      segment.push(`${x(i)},${y(value)}`);
      const dot = svgNode("circle", { cx: x(i), cy: y(value), r: 4, fill: color });
      dot.append(svgNode("title", {}, `${field}, round ${row.round}: ${percent(value)}`));
      svg.append(dot);
    });
    flush();
  }
  chart.append(svg);
  const legend = node("div", "chart-legend");
  const accuracy = node("span"); accuracy.append(node("i"), document.createTextNode("Top-1 plan accuracy"));
  const success = node("span"); success.append(node("i", "secondary-line"), document.createTextNode("Task success"));
  legend.append(accuracy, success);
  chart.append(legend);
}

async function setupEvals() {
  try {
    const data = await api("/api/evals");
    if (!data.available || !data.results) {
      const empty = $("eval-empty");
      empty.replaceChildren(node("strong", "", "No measured results yet."), node("p", "", "Run the golden set against the seeded HydraDB stack to generate an evaluation artifact."), node("code", "", "python evals/run.py --reps 3"));
      return;
    }
    const results = data.results;
    $("eval-empty").hidden = true;
    $("eval-results").hidden = false;
    const meta = $("eval-meta");
    for (const [label, value] of [["Tasks", results.n], ["Repetitions", results.reps], ["Backend", results.backend], ["Commit", results.revision || results.sha || results.commit], ["Local changes", results.dirty_worktree ? "yes" : undefined], ["Catalog", results.catalog_hash?.slice(0, 12)]]) {
      if (value !== undefined && value !== null) meta.append(node("span", "", `${label}: ${value}`));
    }
    const rows = results.families || [];
    const table = $("eval-table");
    rows.forEach((row) => {
      const tr = node("tr");
      tr.dataset.variant = row.variant;
      const variants = { bm25: "BM25 order", static: "Static graph", learning: "Learned graph", switchboard: "Switchboard", graph: "Static graph" };
      const variantLabel = `${variants[row.variant] || row.variant}${row.split ? ` · ${row.split}` : ""}${row.n ? ` (n=${row.n})` : ""}`;
      [row.family, variantLabel, percent(row.precision), percent(row.recall), percent(row.order_validity), percent(row.task_success), fmt(row.tokens), fmt(row.graph_queries)].forEach((value) => tr.append(node("td", "", String(value ?? "—"))));
      table.append(tr);
    });
    if (!rows.length) {
      const row = node("tr");
      const cell = node("td", "", "This artifact has no family aggregates. Inspect the complete artifact below.");
      cell.colSpan = 8;
      row.append(cell); table.append(row);
    }
    renderLearning(results.learning || []);
    setText("eval-json", JSON.stringify(results, null, 2));
  } catch (error) {
    $("eval-empty").hidden = true;
    showError("eval-error", error);
  }
}

async function setupTrace() {
  const id = window.location.pathname.split("/").filter(Boolean).at(-1);
  try {
    if (!id || id === "traces") throw new Error("No trace identifier was supplied.");
    const trace = await api(`/trace/${encodeURIComponent(id)}`);
    renderTrace(trace);
    renderChain(trace.plan || { steps: trace.steps }, trace);
    setText("trace-heading-id", trace.id || id);
  } catch (error) {
    showError("trace-error", error);
    renderTrace({ status: "error", steps: [], error: error.message });
  }
}

health();
if (page === "compare") setupCompare();
if (page === "catalog") setupCatalog();
if (page === "evals") setupEvals();
if (page === "trace") setupTrace();

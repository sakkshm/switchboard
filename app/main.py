"""HTTP demo and stateless MCP gateway over the same planner and executor."""
import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field

from app.executor import Executor
from app.metrics import Metrics, bounded_context, token_count, tool_context
from app.planner import NoPlan, Planner
from app.store import GraphStore
from catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("switchboard")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class RunRequest(BaseModel):
    inputs: dict = Field(default_factory=dict)
    principal: str = Field(default="demo", min_length=1, max_length=128)
    confirmation_token: str | None = None


class TaskRequest(RunRequest):
    task: str = Field(min_length=1, max_length=4000)
    plan_id: str | None = None
    reuse: bool = True
    context_budget_tokens: int = Field(default=3000, ge=128, le=32768)


def create_app(store=None, catalog=None, transport=None):
    catalog = catalog if catalog is not None else load_catalog(os.getenv("SWITCHBOARD_CATALOG"))
    store = store if store is not None else GraphStore(catalog)
    planner, executor = Planner(store, catalog), Executor(store, catalog, transport=transport)
    plans, traces, metrics = OrderedDict(), OrderedDict(), Metrics()

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(token_count, tool_context(catalog))
        try:
            await asyncio.to_thread(store.restore_plans)
        except Exception as exc:
            log.warning("Saved plan restoration unavailable: %s", type(exc).__name__)
        yield

    app = FastAPI(title="Switchboard", lifespan=lifespan)
    app.state.store, app.state.executor, app.state.planner = store, executor, planner

    def remember(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 500:
            cache.popitem(last=False)

    async def make_plan(body):
        try:
            plan = await asyncio.to_thread(planner.plan, body.task, body.principal, body.inputs, body.reuse, body.context_budget_tokens)
        except NoPlan as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            log.exception("Planning failed")
            raise HTTPException(503, "Graph backend unavailable; check /readyz and seed the catalog") from exc
        remember(plans, plan["key"], plan)
        metrics.planned(plan)
        return plan

    async def execute(plan, body):
        trace = await executor.execute(plan, body.inputs, body.principal, body.confirmation_token)
        remember(traces, trace["id"], trace)
        metrics.observe(trace, plan)
        log.info(json.dumps({"request_id": trace["id"], "stage": "run", "ms": trace["metrics"]["wall_ms"], "verdict": trace["status"]}))
        return trace

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        try:
            rows = await asyncio.to_thread(store.hydra.query, "MATCH (t:Tool) RETURN count(*) AS n")
            count = int(rows[0][0]) if rows else 0
            return JSONResponse({"ready": count >= len(catalog), "tools": count, "expected_tools": len(catalog)}, status_code=200 if count >= len(catalog) else 503)
        except Exception:
            return JSONResponse({"ready": False, "error": "HydraDB is unavailable"}, status_code=503)

    @app.post("/plan")
    async def plan_route(body: TaskRequest):
        return await make_plan(body)

    @app.post("/run/{plan_id}")
    async def run_route(plan_id: str, body: RunRequest):
        if plan_id not in plans:
            raise HTTPException(404, "Unknown plan; call find or /plan first")
        return await execute(dict(plans[plan_id], graph_queries=0, reused=True), body)

    @app.post("/confirm/{plan_id}")
    async def confirm(plan_id: str, body: RunRequest):
        if plan_id not in plans:
            raise HTTPException(404, "Unknown plan")
        try:
            allowed = await asyncio.to_thread(store.grants, body.principal)
        except Exception as exc:
            raise HTTPException(503, "Graph backend unavailable") from exc
        if not all(step["tool"] in allowed for step in plans[plan_id]["steps"]):
            raise HTTPException(403, "Plan is not permitted")
        return {"confirmation_token": executor.confirmations.issue(plan_id, body.principal), "expires_in": 300}

    @app.post("/compare")
    async def compare(body: TaskRequest):
        started = time.perf_counter()
        if body.plan_id:
            if body.plan_id not in plans:
                raise HTTPException(404, "Unknown plan")
            plan = dict(plans[body.plan_id], graph_queries=0, reused=True)
        else:
            plan = await make_plan(body)
        trace = await execute(plan, body)
        selected = [planner.tools[s["tool"]] for s in plan["steps"]]
        return {"baseline": {"tools": len(catalog), "tokens": token_count(tool_context(catalog)), "label": "Static catalog cost, measured", "order_validity": None, "encoding": "cl100k_base", "live_execution": False},
                "switchboard": {"tools": len(selected), "tokens": token_count(tool_context(selected)), "graph_queries": plan["graph_queries"], "tool_calls": trace["metrics"]["tool_calls"], "wall_ms": (time.perf_counter()-started)*1000, "reused": plan["reused"], "model_calls": 0}, "plan": plan, "trace": trace}

    @app.get("/trace/{trace_id}")
    def get_trace(trace_id: str):
        if trace_id not in traces:
            raise HTTPException(404, "Trace not retained in this process")
        return traces[trace_id]

    @app.get("/metrics", response_class=PlainTextResponse)
    def get_metrics():
        return metrics.render(getattr(getattr(store, "hydra", None), "query_count", 0))

    @app.get("/api/catalog")
    async def get_catalog(principal: str = "demo"):
        try:
            allowed = await asyncio.to_thread(store.grants, principal)
            usage = await asyncio.to_thread(store.plan_usage)
        except Exception as exc:
            raise HTTPException(503, "Graph backend unavailable") from exc
        return {"tools": [t for t in catalog if t["key"] in allowed], "plan_usage": {key: value for key, value in usage.items() if key in allowed}}

    @app.get("/api/evals")
    def get_evals():
        files = sorted((ROOT / "evals" / "results").glob("*.json"), key=lambda p: p.stat().st_mtime)
        bundled = ROOT / "evals" / "latest.json"
        if bundled.exists():
            files.insert(0, bundled)
        for path in reversed(files):
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("families"):
                return {"available": True, "results": result}
        return {"available": False, "results": None}

    mcp_tools = [
        {"name": "find", "description": "Find permitted tools and a graph plan for a task. Returns a plan_id for run.", "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "inputs": {"type": "object"}}, "required": ["task"], "additionalProperties": False}},
        {"name": "describe", "description": "Read the JSON Schema for one permitted tool.", "inputSchema": {"type": "object", "properties": {"tool": {"type": "string"}}, "required": ["tool"], "additionalProperties": False}},
        {"name": "run", "description": "Execute a previously returned graph plan with fresh input arguments.", "inputSchema": {"type": "object", "properties": {"plan_id": {"type": "string"}, "inputs": {"type": "object"}, "confirmation_token": {"type": "string"}}, "required": ["plan_id", "inputs"], "additionalProperties": False}},
    ]

    @app.post("/mcp")
    async def mcp(request: Request):
        origin = request.headers.get("origin")
        allowed_origins = set(os.getenv("SWITCHBOARD_ALLOWED_ORIGINS", "http://localhost:18000,http://127.0.0.1:18000").split(","))
        if origin and origin not in allowed_origins:
            return JSONResponse({"error": "Origin not permitted"}, status_code=403)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}, status_code=400)
        request_id, method = payload.get("id"), payload.get("method")
        if "id" not in payload:
            return Response(status_code=202)
        principal = request.headers.get("x-agent-key", "demo")
        try:
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}}
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "switchboard", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": mcp_tools}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments", {})
                tool_schema = next((tool["inputSchema"] for tool in mcp_tools if tool["name"] == name), None)
                if tool_schema is None:
                    raise HTTPException(404, "Unknown gateway tool")
                try:
                    Draft202012Validator(tool_schema).validate(args)
                except ValidationError:
                    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
                if name == "find":
                    plan = await make_plan(TaskRequest(task=args["task"], inputs=args.get("inputs", {}), principal=principal))
                    value = {"plan_id": plan["key"], "tools": [{"name": s["tool"], "description": planner.tools[s["tool"]]["description"].split("[")[0].strip()} for s in plan["steps"]], "evidence": plan["evidence"], "reused": plan["reused"]}
                elif name == "describe":
                    allowed = await asyncio.to_thread(store.grants, principal)
                    key = args["tool"]
                    if key not in allowed or key not in planner.tools:
                        raise HTTPException(404, "Unknown tool")
                    value = planner.tools[key]["inputSchema"]
                elif name == "run":
                    value = await run_route(args["plan_id"], RunRequest(inputs=args["inputs"], principal=principal, confirmation_token=args.get("confirmation_token")))
                    value = {"trace_id": value["id"], "status": value["status"],
                             "result": value.get("result"), "error": value.get("error"),
                             "metrics": value["metrics"]}
                else:
                    raise HTTPException(404, "Unknown gateway tool")
                result = {"content": [{"type": "text", "text": bounded_context(value)}], "isError": isinstance(value, dict) and value.get("status") in {"error", "blocked"}}
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (HTTPException, KeyError, ValueError) as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": message}], "isError": True}}
        except Exception:
            log.exception("MCP call failed")
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Graph gateway unavailable"}}

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/catalog")
    def catalog_page():
        return FileResponse(ROOT / "static" / "catalog.html")

    @app.get("/evals")
    def evals_page():
        return FileResponse(ROOT / "static" / "evals.html")

    @app.get("/traces/{trace_id}")
    def trace_page(trace_id: str):
        return FileResponse(ROOT / "static" / "trace.html")

    return app


app = create_app()

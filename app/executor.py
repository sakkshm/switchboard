"""Execute graph plans with runtime grants, schema checks and append-only audit."""

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.binder import BindingError, bind
from app.grants import (
    AccessDenied,
    ConfirmationTokens,
    RateLimited,
    TokenBucket,
    check_grant,
)
from app.transport import MCPTransport, TransportError

logger = logging.getLogger("switchboard.executor")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def catalog_hash(catalog):
    tools = list(catalog.values()) if isinstance(catalog, dict) else catalog
    return hashlib.sha256(
        canonical(sorted(tools, key=lambda tool: tool["key"])).encode()
    ).hexdigest()


def _strict_schema(schema):
    schema = copy.deepcopy(schema)

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value and not str(value["$ref"]).startswith("#"):
                raise ValueError("Only local JSON Schema references are supported")
            if value.get("type") == "object" or "properties" in value:
                value.setdefault("additionalProperties", False)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(schema)
    Draft202012Validator.check_schema(schema)
    return schema


def clean_output(value):
    if isinstance(value, str):
        return CONTROL.sub("", value)
    if isinstance(value, list):
        return [clean_output(item) for item in value]
    if isinstance(value, dict):
        return {
            CONTROL.sub("", str(key)): clean_output(item) for key, item in value.items()
        }
    return value


class Executor:
    def __init__(
        self,
        store,
        catalog,
        transport=None,
        timeout=10,
        max_output_bytes=65_536,
        rate_limit=30,
        concurrency=8,
        confirmations=None,
        learning=True,
    ):
        self.store = store
        self.catalog = (
            {tool["key"]: tool for tool in catalog}
            if isinstance(catalog, list)
            else catalog
        )
        self.catalog_hash = catalog_hash(self.catalog)
        self.transport = transport if transport is not None else MCPTransport()
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.limiter = TokenBucket(
            capacity=rate_limit, refill_per_second=rate_limit / 60
        )
        self.semaphore = asyncio.Semaphore(concurrency)
        self.confirmations = confirmations or ConfirmationTokens()
        self.learning = learning
        self.schemas = {
            key: Draft202012Validator(
                _strict_schema(tool.get("inputSchema", {"type": "object"}))
            )
            for key, tool in self.catalog.items()
        }

    async def _store(self, method, *args, **kwargs):
        function = getattr(self.store, method)
        if inspect.iscoroutinefunction(function):
            return await function(*args, **kwargs)
        return await asyncio.to_thread(function, *args, **kwargs)

    async def _audit(self, trace, tool, verdict, reason, args=None):
        decision = {
            "key": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "request_id": trace["id"],
            "agent": trace["principal"],
            "tool": tool,
            "verdict": verdict,
            "reason": reason,
            "args_hash": hashlib.sha256(canonical(args or {}).encode()).hexdigest(),
        }
        await self._store("audit", decision)

    def _preflight(self, plan, inputs):
        if not isinstance(plan, dict) or not isinstance(plan.get("key"), str):
            raise AccessDenied("Malformed plan")
        if plan.get("catalog_hash") != self.catalog_hash:
            raise AccessDenied("Plan catalog version is stale; create a fresh plan")
        if not isinstance(inputs, dict):
            raise BindingError("Inputs must be an object")
        if not isinstance(plan.get("steps"), list) or not 1 <= len(plan["steps"]) <= 7:
            raise AccessDenied("Plan must have between one and seven steps")
        for index, step in enumerate(plan["steps"]):
            if not isinstance(step, dict) or step.get("i") != index:
                raise AccessDenied(
                    "Plan steps must be ordered and consecutively indexed"
                )
            if step.get("tool") not in self.catalog:
                raise AccessDenied("Unknown tool in plan")
            if not isinstance(step.get("args"), dict):
                raise BindingError("Step arguments must be an object")

    async def execute(self, plan, inputs, principal="demo", confirmation_token=None):
        started = time.perf_counter()
        trace = {
            "id": uuid.uuid4().hex,
            "plan_id": plan.get("key") if isinstance(plan, dict) else None,
            "principal": principal,
            "status": "running",
            "steps": [],
            "metrics": {"tool_calls": 0, "wall_ms": 0, "model_calls": 0},
        }
        current = None
        called = False
        try:
            if not isinstance(principal, str) or not principal or len(principal) > 128:
                raise AccessDenied("A valid demo principal is required")
            self.limiter.consume(principal)
            self._preflight(plan, inputs)
            for index, step in enumerate(plan["steps"]):
                tool = self.catalog[step["tool"]]
                current = {"i": index, "tool": tool["key"], "status": "pending"}
                trace["steps"].append(current)
                called = False
                args = bind(step["args"], inputs, trace["steps"][:-1], index)
                self.schemas[tool["key"]].validate(args)
                current["args"] = args
                step_started = time.perf_counter()
                # The wait for a concurrency slot is also bounded by the deadline.
                async with asyncio.timeout(self.timeout):
                    async with self.semaphore:
                        # Check after acquiring the slot, so queued plans cannot
                        # retain permission revoked while they were waiting.
                        granted = await self._store("grants", principal)
                        check_grant(
                            tool,
                            granted,
                            self.confirmations.verify(
                                confirmation_token, plan["key"], principal
                            ),
                        )
                        # If durable audit is unavailable, fail before upstream.
                        await self._audit(
                            trace,
                            tool["key"],
                            "allow",
                            "grant and schema checks passed",
                            args,
                        )
                        trace["metrics"]["tool_calls"] += 1
                        called = True
                        result = await self.transport.call(tool, args)
                if len(canonical(result).encode()) > self.max_output_bytes:
                    raise TransportError("Tool output exceeds the configured size cap")
                result = clean_output(result)
                current.update(
                    status="success",
                    result=result,
                    ms=round((time.perf_counter() - step_started) * 1000, 3),
                )
                await self._audit(trace, tool["key"], "success", "tool completed", args)
            chain = [step["tool"] for step in plan["steps"]]
            if self.learning:
                await self._store("learn", chain, True)
            await self._store("save_plan", plan, principal)
            trace["status"] = "success"
            trace["result"] = trace["steps"][-1]["result"]
        except Exception as exc:  # noqa: BLE001 -- execution boundary must return a failed trace
            blocked = isinstance(exc, (AccessDenied, BindingError, ValidationError))
            code = (
                "rate_limited"
                if isinstance(exc, RateLimited)
                else (
                    "timeout"
                    if isinstance(exc, TimeoutError)
                    else "blocked"
                    if blocked
                    else "execution_error"
                )
            )
            message = (
                "Tool call exceeded its deadline"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
            message = CONTROL.sub("", message)[:1000]
            trace["status"] = "blocked" if blocked else "error"
            trace["error"] = {"code": code, "message": message}
            # Keep a completed result when persistence fails after a side effect.
            if current is not None and current["status"] != "success":
                current.update(status=trace["status"], error=message)
            try:
                await self._audit(
                    trace,
                    current["tool"] if current else "*",
                    trace["status"],
                    message,
                    current.get("args") if current else None,
                )
            except Exception:  # noqa: BLE001 -- preserve the primary failure if audit is down
                trace["audit_error"] = "Could not persist the failure decision"
            if (
                self.learning
                and called
                and current
                and current["status"] != "success"
                and not blocked
            ):
                try:
                    await self._store(
                        "learn",
                        [step["tool"] for step in plan["steps"]],
                        False,
                        failed_index=current["i"],
                    )
                except Exception:  # noqa: BLE001 -- preserve the primary upstream failure
                    trace["learning_error"] = (
                        "Could not persist the failed-step learning signal"
                    )
        finally:
            trace["metrics"]["wall_ms"] = round(
                (time.perf_counter() - started) * 1000, 3
            )
            logger.info(
                json.dumps(
                    {
                        "request_id": trace["id"],
                        "stage": "execute",
                        "ms": trace["metrics"]["wall_ms"],
                        "verdict": trace["status"],
                    }
                )
            )
        return trace

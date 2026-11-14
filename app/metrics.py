"""Measured schema costs (cl100k_base), plus execution counters."""

import json
import threading
from collections import Counter
from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def encoding():
    return tiktoken.get_encoding("cl100k_base")


def token_count(value):
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    return len(encoding().encode(text, disallowed_special=()))


def tool_context(tools):
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in tools]


def bounded_context(value, budget=3000):
    """Bound JSON context while retaining a run's machine-readable outcome.

    Large run results keep trace_id/status/metrics/error outside the preview.
    A budget too small for that envelope is rejected rather than hiding the
    trace reference or changing the outcome.
    """
    if budget < 64:
        raise ValueError("Context budget must be at least 64 tokens")
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tokens = encoding().encode(text, disallowed_special=())
    if len(tokens) <= budget:
        return text
    run = isinstance(value, dict) and "trace_id" in value and "status" in value
    preserved = {key: value.get(key) for key in ("trace_id", "status", "metrics", "error")} if run else {}
    source = json.dumps(value.get("result"), ensure_ascii=False, separators=(",", ":")) if run else text
    source_tokens = encoding().encode(source, disallowed_special=())

    def render(length):
        preview = {
            "truncated": True,
            "original_tokens": len(tokens),
            "preview": encoding().decode(source_tokens[:length]),
        }
        wrapped = {**preserved, "result": preview} if run else preview
        return json.dumps(wrapped, ensure_ascii=False, separators=(",", ":"))

    best = render(0)
    if token_count(best) > budget:
        raise ValueError("Context budget cannot hold the execution outcome metadata")
    low, high = 0, min(len(source_tokens), budget)
    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if token_count(candidate) <= budget:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


class Metrics:
    def __init__(self):
        self.counts = Counter()
        self.lock = threading.Lock()

    def observe(self, trace, plan):
        with self.lock:
            self.counts["runs_total"] += 1
            self.counts["tool_calls_total"] += trace["metrics"]["tool_calls"]
            self.counts["reuse_hits_total"] += int(plan.get("reused", False))
            self.counts[f"runs_{trace['status']}_total"] += 1

    def planned(self, plan):
        with self.lock:
            self.counts["planning_graph_queries_total"] += plan.get("graph_queries", 0)

    def render(self, graph_queries):
        with self.lock:
            lines = [f"switchboard_{key} {value}" for key, value in sorted(self.counts.items())]
        return (
            "\n".join(lines + [f"switchboard_backend_queries_total {graph_queries}", "switchboard_model_calls_total 0"])
            + "\n"
        )

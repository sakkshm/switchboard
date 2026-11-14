"""Select an executable, graph-returned order. No local ordering fallback."""

import copy
import hashlib
import json
import time

from app.executor import catalog_hash
from app.matcher import Matcher
from app.metrics import token_count, tool_context
from app.validate import validate_order


class NoPlan(ValueError):
    pass


class Planner:
    def __init__(self, store, catalog, learning=True):
        self.store = store
        self.catalog = list(catalog)
        self.tools = {t["key"]: t for t in catalog}
        self.matcher = Matcher(catalog)
        self.catalog_hash = catalog_hash(catalog)
        self.learning = learning

    def plan(self, task, principal="demo", inputs=None, reuse=True, context_budget_tokens=3000, input_types=()):
        started = time.perf_counter()
        inputs = inputs or {}
        if not 128 <= context_budget_tokens <= 32768:
            raise ValueError("Context budget must be between 128 and 32768 tokens")
        # Input values are rebound every run; their shape is part of the plan identity.
        normalized = " ".join(task.lower().split())
        intent_hash = hashlib.sha256(json.dumps(
            [normalized, sorted(inputs), self.catalog_hash, sorted(input_types)], separators=(",", ":")
        ).encode()).hexdigest()
        allowed = self.store.grants(principal)
        if reuse:
            cached = self.store.load_plan(intent_hash, principal)
            if (cached and cached.get("catalog_hash") == self.catalog_hash
                    and all(s["tool"] in allowed for s in cached["steps"])):
                if token_count(tool_context([self.tools[s["tool"]] for s in cached["steps"]])) > context_budget_tokens:
                    raise NoPlan("The complete tool chain exceeds the context budget")
                cached.update(reused=True, graph_queries=0, context_budget_tokens=context_budget_tokens,
                              plan_ms=(time.perf_counter()-started)*1000)
                return cached
        # Match intent before filtering: do not silently turn a denied write into a read.
        match = self.matcher.match(task)
        goal = match["goal"]
        if not goal or goal not in allowed:
            self.store.audit({"agent": principal, "tool": "", "verdict": "deny", "reason": "no permitted plan"})
            raise NoPlan("No permitted plan matches this task")
        candidates = [key for key in match["candidates"] if key in allowed]
        ranked = self.matcher.rank(task, allowed=allowed, limit=10)
        ranks = {item["key"]: item["score"] for item in ranked}
        singleton = not self.tools[goal]["consumes"]
        if singleton:
            paths = [{"chain": [goal], "weights": []}] if self.store.exists(goal) else []
        else:
            paths = self.store.paths(candidates, goal)
        scored = []
        for path in paths:
            chain = path["chain"]
            if any(key not in allowed for key in chain) or validate_order(chain, self.tools, input_types) is not None:
                continue
            coverage = len(set(chain) & set(candidates))
            score = coverage - 0.1 * (len(chain)-1)
            if self.learning:
                score += sum(path.get("weights", []))
            # Text relevance breaks equal path scores, but never supplies an order.
            score += sum(ranks.get(key, 0) for key in chain) * 0.001
            scored.append((score, chain, path))
        if not scored:
            raise NoPlan("No executable path exists in the graph for this task")
        scored.sort(key=lambda row: (-row[0], row[1]))
        score, chain, path = scored[0]
        context_tokens = token_count(tool_context([self.tools[key] for key in chain]))
        if context_tokens > context_budget_tokens:
            raise NoPlan("The complete tool chain exceeds the context budget")
        plan_key = hashlib.sha256(json.dumps([intent_hash, chain]).encode()).hexdigest()[:24]
        return {"key": plan_key, "intent": task, "intent_hash": intent_hash,
                "principal": principal, "catalog_hash": self.catalog_hash,
                "steps": [{"i": i, "tool": key, "args": copy.deepcopy(self.tools[key]["args"])}
                          for i, key in enumerate(chain)],
                "values": {}, "wins": 0, "graph_queries": 1, "reused": False,
                "plan_ms": (time.perf_counter()-started)*1000,
                "context_tokens": context_tokens, "context_budget_tokens": context_budget_tokens,
                "evidence": {"source": "HydraDB Tool membership" if singleton else "HydraDB algo.MSpaths", "score": score,
                             "weights": path.get("weights", []), "type_valid": True}}

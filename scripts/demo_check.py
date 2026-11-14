"""Headless checks against a running real Switchboard stack; synthetic writes only."""
import argparse
import json
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    def call(path, payload=None):
        response = requests.get(url + path, timeout=30) if payload is None else requests.post(url + path, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    readiness = call("/readyz")
    assert readiness["ready"] and readiness["tools"] == 60, readiness
    tasks = [json.loads(line) for line in (ROOT / "evals/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    families, evidence = set(), []
    for task in tasks:
        if task["family"] in families:
            continue
        families.add(task["family"])
        body = {"task": task["task"] + " verification " + uuid.uuid4().hex,
                "inputs": task["expected_values"], "principal": "demo"}
        if task.get("confirmation_token"):
            plan = call("/plan", body)
            token = call("/confirm/" + plan["key"], {"principal": "demo"})["confirmation_token"]
            body.update(plan_id=plan["key"], confirmation_token=token)
        first = call("/compare", body)
        assert first["trace"]["status"] == "success", first["trace"]
        assert [step["tool"] for step in first["plan"]["steps"]] == task["expected_chain"]
        assert first["baseline"]["tools"] == 60 and first["switchboard"]["tools"] == 4
        assert first["switchboard"]["tokens"] < first["baseline"]["tokens"]
        for key, value in task["expected_end_state"].items():
            assert first["trace"]["result"][key] == value
        second = call("/compare", body)
        assert second["trace"]["status"] == "success", second["trace"]
        assert second["switchboard"]["graph_queries"] == 0 and second["switchboard"]["reused"]
        blocked = call("/compare", dict(body, principal="readonly", plan_id=first["plan"]["key"]))
        assert blocked["trace"]["status"] == "blocked", blocked["trace"]
        assert blocked["trace"]["steps"][-1]["status"] == "blocked"
        assert call("/trace/" + first["trace"]["id"])["status"] == "success"
        evidence.append({"family": task["family"], "status": "passed", "first": first["switchboard"],
                         "repeat": second["switchboard"], "readonly": blocked["trace"]["status"],
                         "baseline_tokens": first["baseline"]["tokens"]})
        print(f"PASS {task['family']}: execution, reuse, readonly, trace", flush=True)
    discovery = call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert [tool["name"] for tool in discovery["result"]["tools"]] == ["find", "describe", "run"]
    assert len(call("/api/catalog")["tools"]) == 60
    destination = ROOT / "results" / "demo-check.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps({"backend": "real HydraDB OSS", "families": evidence, "mcp_tools": 3}, indent=2) + "\n", encoding="utf-8")
    print(f"PASS 4 families and 3-tool gateway; evidence: {destination}")


if __name__ == "__main__":
    main()

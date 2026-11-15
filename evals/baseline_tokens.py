"""Count the tool-schema context a client pays for: 60 native tools vs 2 gateway tools.

Reads the real MCP `tools/list` objects from the configured upstream servers and
from `switchboard serve`, then serializes them the same way for both sides.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from switchboard import config, executor  # noqa: E402

RESULTS = ROOT / "evals" / "results"
ENCODING = tiktoken.get_encoding("cl100k_base")


def payload(tools):
    """Canonical tools/list payload: the fields a client actually receives."""
    items = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in tools
    ]
    return json.dumps({"tools": items}, separators=(",", ":"), sort_keys=True)


def measure(tools):
    text = payload(tools)
    return {
        "tools": len(tools),
        "characters": len(text),
        "tokens": len(ENCODING.encode(text)),
    }


async def collect(servers, gateway_command=None):
    native = []
    for name, server in servers.items():
        tools = await executor.list_tools(server)
        native.append({"server": name, **measure(tools)})
    total = {
        "tools": sum(row["tools"] for row in native),
        "characters": sum(row["characters"] for row in native),
        "tokens": sum(row["tokens"] for row in native),
    }
    gateway = None
    if gateway_command:
        tools = await executor.list_tools(
            {
                "transport": "stdio",
                "command": gateway_command,
                "cwd": str(ROOT),
                "env": dict(os.environ),
            }
        )
        gateway = measure(tools)
    return {"native": native, "native_total": total, "gateway": gateway}


async def main():
    current = config.load()
    if not current["servers"]:
        print("switchboard: no upstreams configured", file=sys.stderr)
        return 1
    command = [str(Path(sys.executable).with_name("switchboard")), "serve"]
    report = await collect(current["servers"], command)
    report["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if report["gateway"]:
        report["savings"] = {
            "characters": report["native_total"]["characters"] - report["gateway"]["characters"],
            "tokens": report["native_total"]["tokens"] - report["gateway"]["tokens"],
            "ratio": round(
                report["native_total"]["tokens"] / report["gateway"]["tokens"], 1
            ),
        }

    for row in report["native"]:
        print(f"{row['server']:10} {row['tools']:3} tools {row['characters']:6} chars {row['tokens']:6} tokens")
    total = report["native_total"]
    print(f"{'native':10} {total['tools']:3} tools {total['characters']:6} chars {total['tokens']:6} tokens")
    if report["gateway"]:
        row = report["gateway"]
        print(f"{'gateway':10} {row['tools']:3} tools {row['characters']:6} chars {row['tokens']:6} tokens")

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "tokens.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

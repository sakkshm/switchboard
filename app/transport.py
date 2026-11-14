"""Bounded MCP stdio transport using only administrator-configured servers.

Spec: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
One short-lived initialized session per call keeps cancellation deterministic.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2025-03-26"


class TransportError(RuntimeError):
    pass


class MCPTransport:
    def __init__(
        self, config_path=None, servers=None, timeout=10, max_message_bytes=1_048_576
    ):
        if servers is None:
            path = Path(config_path or os.getenv("SWITCHBOARD_SERVERS") or ROOT / "catalog" / "servers.yaml")
            servers = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.servers = servers.get("servers", servers)
        self.timeout = timeout
        self.max_message_bytes = max_message_bytes

    def _config(self, server):
        if server not in self.servers or not self.servers[server].get("enabled", True):
            raise TransportError("Tool server is not on the configured allowlist")
        config = self.servers[server]
        command = config.get("command", "")
        args = config.get("args", [])
        if not isinstance(command, str) or not command or not isinstance(args, list):
            raise TransportError("Invalid MCP server configuration")
        if any(not isinstance(arg, str) for arg in args):
            raise TransportError("MCP process arguments must be strings")
        def expand(value):
            return value.replace("{python}", sys.executable).replace("{root}", str(ROOT))
        environment = os.environ.copy()
        environment.update(
            {key: expand(str(value)) for key, value in config.get("env", {}).items()}
        )
        return (
            expand(command),
            [expand(arg) for arg in args],
            expand(config.get("cwd", "{root}")),
            environment,
        )

    async def _send(self, process, message):
        process.stdin.write(
            (json.dumps(message, separators=(",", ":")) + "\n").encode()
        )
        await process.stdin.drain()

    async def _rpc(self, process, request_id, method, params):
        await self._send(
            process,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        for _ in range(100):
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as exc:
                raise TransportError("MCP response exceeds the message limit") from exc
            if not line:
                raise TransportError("MCP server closed before answering")
            if len(line) > self.max_message_bytes:
                raise TransportError("MCP response exceeds the message limit")
            try:
                response = json.loads(line)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise TransportError("MCP server returned invalid JSON") from exc
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                raise TransportError("MCP server returned an invalid response")
            if "method" in response and "id" not in response:
                continue
            if response.get("method") == "ping" and "id" in response:
                await self._send(
                    process, {"jsonrpc": "2.0", "id": response["id"], "result": {}}
                )
                continue
            if "method" in response and "id" in response:
                await self._send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": response["id"],
                        "error": {
                            "code": -32601,
                            "message": "Client method not supported",
                        },
                    },
                )
                continue
            if response.get("id") != request_id:
                raise TransportError("MCP response id does not match request")
            if "error" in response:
                error = response["error"]
                raise TransportError(
                    str(error.get("message", "MCP server error"))[:500]
                )
            if not isinstance(response.get("result"), dict):
                raise TransportError("MCP result must be an object")
            return response["result"]
        raise TransportError("Too many MCP notifications without a response")

    async def request(self, server, method, params=None):
        command, args, cwd, environment = self._config(server)
        process = None
        try:
            async with asyncio.timeout(self.timeout):
                process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    cwd=cwd,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=self.max_message_bytes,
                )
                initialized = await self._rpc(
                    process,
                    1,
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "switchboard", "version": "0.1.0"},
                    },
                )
                if initialized.get("protocolVersion") not in {
                    PROTOCOL_VERSION,
                    "2025-06-18",
                    "2025-11-25",
                }:
                    raise TransportError(
                        "MCP server negotiated an unsupported protocol version"
                    )
                if "tools" not in initialized.get("capabilities", {}):
                    raise TransportError("MCP server does not offer tools")
                await self._send(
                    process, {"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
                return await self._rpc(process, 2, method, params or {})
        except TimeoutError:
            raise
        except OSError as exc:
            raise TransportError(
                f"Could not start configured MCP server: {exc.strerror}"
            ) from exc
        finally:
            if process is not None:
                if process.stdin:
                    process.stdin.close()
                if process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), 0.1)
                    except TimeoutError:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()

    async def list_tools(self, server):
        tools = []
        cursor = None
        seen = set()
        while True:
            result = await self.request(
                server, "tools/list", {"cursor": cursor} if cursor else {}
            )
            batch = result.get("tools")
            if not isinstance(batch, list):
                raise TransportError("MCP tools/list did not return tools")
            tools.extend(batch)
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen or len(tools) > 10000:
                raise TransportError("MCP catalog pagination exceeded its bounds")
            seen.add(cursor)

    async def call(self, tool, args):
        result = await self.request(
            tool["server"], "tools/call", {"name": tool["name"], "arguments": args}
        )
        if result.get("isError", result.get("is_error", False)):
            messages = [
                item.get("text", "")
                for item in result.get("content", [])
                if isinstance(item, dict)
            ]
            raise TransportError(
                "; ".join(messages)[:500] or "MCP tool reported an error"
            )
        structured = result.get("structuredContent", result.get("structured_content"))
        if structured is not None:
            return structured
        content = result.get("content", [])
        if (
            len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
        ):
            try:
                return json.loads(content[0].get("text", ""))
            except json.JSONDecodeError:
                return content[0].get("text", "")
        return content

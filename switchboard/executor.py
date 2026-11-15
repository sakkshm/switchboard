"""Upstream MCP transport and tool calls. Reconnect per call for P0."""

import json
import tempfile
from contextlib import asynccontextmanager

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config

TIMEOUT = 10


def _stderr_reason(errlog):
    """The upstream's own reason: its first exception line, else its last line."""
    errlog.seek(0)
    lines = [line.strip() for line in errlog.read().splitlines() if line.strip()]
    for line in lines:
        name = line.split(":", 1)[0]
        if name.rsplit(".", 1)[-1].endswith(("Error", "Exception")):
            return line
    return lines[-1] if lines else ""


def _leaf(exc):
    """Unwrap the SDK's single-child ExceptionGroups down to the real error."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


@asynccontextmanager
async def connect(server):
    if server.get("transport") == "http" or "url" in server:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers = config.resolve(server.get("headers", {}))
        async with httpx2.AsyncClient(headers=headers) as http, Client(
            streamable_http_client(server["url"], http_client=http),
            read_timeout_seconds=TIMEOUT,
        ) as client:
            yield client
        return

    command = config.resolve(server.get("command", []))
    if not command:
        raise RuntimeError("upstream server has no command")
    params = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env=config.resolve(server.get("env") or {}) or None,
        cwd=server.get("cwd"),
    )
    # Capture the child's stderr instead of letting its tracebacks flood the
    # terminal; on failure the last line becomes the reason we report.
    with tempfile.TemporaryFile(mode="w+") as errlog:
        try:
            async with Client(stdio_client(params, errlog=errlog), read_timeout_seconds=TIMEOUT) as client:
                yield client
        except Exception as exc:
            leaf = _leaf(exc)
            reason = _stderr_reason(errlog)
            message = str(leaf)
            if reason and reason not in message:
                message = f"{message} - {reason}"
            raise RuntimeError(message) from leaf


async def list_tools(server):
    async with connect(server) as client:
        return (await client.list_tools()).tools


def _blocks_text(blocks):
    """Text from content blocks; a resource's text wins over status text."""
    text, resource = [], []
    for block in blocks:
        if not isinstance(block, dict):
            block = block.model_dump()
        if block.get("type") == "resource":
            resource.append(block.get("resource", {}).get("text", ""))
        elif block.get("text") is not None:
            text.append(block["text"])
    return "".join(resource) or "".join(text)


def value(result):
    if result.structured_content is not None:
        structured = result.structured_content
        # Python MCP servers wrap a bare scalar return in {"result": x}
        if isinstance(structured, dict) and list(structured) == ["result"]:
            return structured["result"]
        # Go servers return {"content": [text, resource]} for file reads
        if isinstance(structured, dict) and list(structured) == ["content"] and isinstance(structured["content"], list):
            return _blocks_text(structured["content"])
        return structured
    text = "".join(block.text for block in result.content if getattr(block, "type", "") == "text")
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


async def call(server, tool, arguments):
    # shortcut: one upstream connection per step; keep sessions open if latency matters
    async with connect(server) as client:
        result = await client.call_tool(tool, arguments)
        if result.is_error:
            # raised inside the block so connect() can attach the upstream's stderr
            raise RuntimeError(value(result))
    return value(result)

"""Infer consumes/produces for upstream tools, dep-graph style.

MCP publishes no dependency types, and an output schema is often missing or a
placeholder (every filesystem tool here declares `content`). Layer 1 is
structural: required input parameters become consumes, declared outputs become
candidate produces. Layer 2 asks a model to name the types a tool really
returns, since the schema alone cannot tell a read from a write.

The model backend is the local `opencode` CLI by default, so there is no key to
configure. `SWITCHBOARD_LLM_CMD` replaces the whole command and
`SWITCHBOARD_LLM_MODEL` adds `-m`.
"""

import json
import os
import shlex
import subprocess

INSTRUCTIONS = """\
You label MCP tools with the data types they pass to each other, so a planner
can chain them. Return ONLY a JSON object: {"server.tool": {"produces": [...]}}

A tool produces a type only when it RETURNS a data payload that another tool
would take as input: file or page content, a list of files, an issue, a
repository, a commit. Name each type with one lowercase snake_case word.
Rules:
- Most tools produce nothing. Writers, creators, movers, editors, deleters, and
  tools that only return identifiers produce nothing.
- Never use an identifier or control field as a type name: owner, repo, path,
  ref, sha, branch, tag, page, perPage, query, name, url, message, title, state.
- Reuse an input parameter name when the payload is that kind of data. Example:
  file writers consume the parameter "content", so a tool that fetches or reads
  a page or file produces "content".
- Include every tool; an empty list is correct for a pure writer.

Each tool is `key | description | inputs`. Judge from the description: declared
output schemas are not shown because they are often a placeholder.
"""


def schema_props(schema):
    """(required input names, declared output names) from a JSON schema string."""
    try:
        data = json.loads(schema or "{}")
    except ValueError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    properties = data.get("properties")
    names = list(properties) if isinstance(properties, dict) else []
    required = [name for name in data.get("required") or [] if name in names]
    return required or names, names


def structural(tools):
    """Layer 1: consumes from required inputs, candidate produces from outputs."""
    result = {}
    for tool in tools:
        inputs, _ = schema_props(tool["input_schema"])
        _, declared = schema_props(tool["output_schema"])
        result[tool["key"]] = {"consumes": inputs, "produces": declared}
    return result


def prompt(tools, candidates):
    vocabulary = sorted({name for entry in candidates.values() for name in entry["consumes"]})
    lines = [INSTRUCTIONS, f"INPUT PARAMETERS: {', '.join(vocabulary)}\n", "Tools:"]
    for tool in tools:
        inputs, _ = schema_props(tool["input_schema"])
        description = " ".join((tool["description"] or "").split())[:130]
        lines.append(f"{tool['key']} | {description} | inputs: {', '.join(inputs) or '-'}")
    return "\n".join(lines)


def _text(output):
    chunks = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("text"):
            chunks.append(part["text"])
    return "".join(chunks) or output


def ask(prompt_text, timeout=240):
    command = os.getenv("SWITCHBOARD_LLM_CMD")
    argv = shlex.split(command) if command else ["opencode", "run", "--format", "json"]
    model = os.getenv("SWITCHBOARD_LLM_MODEL")
    if model and not command:
        argv += ["-m", model]
    try:
        proc = subprocess.run([*argv, prompt_text], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ValueError("no model backend: install opencode or set SWITCHBOARD_LLM_CMD") from None
    except subprocess.TimeoutExpired:
        raise ValueError(f"model call timed out after {timeout}s") from None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        raise ValueError(f"model call failed: {tail[-1][:200] if tail else proc.returncode}")
    return _text(proc.stdout)


def _object(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"model did not return JSON: {text.strip()[:200]}")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ValueError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("model JSON is not an object")
    return data


def names(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def infer(tools, ask_model=ask):
    """One model call; returns {key: {consumes, produces}} with non-empty entries."""
    candidates = structural(tools)
    vocabulary = {name for entry in candidates.values() for name in entry["consumes"]}
    data = _object(ask_model(prompt(tools, candidates)))
    result = {}
    for tool in tools:
        key = tool["key"]
        entry = data.get(key) if isinstance(data.get(key), dict) else {}
        # a produced type only matters when some tool's required input can receive it
        produces = [name for name in names(entry.get("produces")) if name in vocabulary]
        fields = {"consumes": candidates[key]["consumes"], "produces": produces}
        if fields["consumes"] or fields["produces"]:
            result[key] = fields
    return result

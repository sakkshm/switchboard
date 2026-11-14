"""Optional read-only MCP adapters for one explicitly configured workspace.

Run ``python -m catalog.local_server --kind filesystem`` or ``--kind git``
with SWITCHBOARD_WORKSPACE set to an absolute directory. Nothing is enabled
implicitly. The trusted sample metadata is in local_catalog.json.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

MAX_FILE_BYTES = 48 * 1024
MAX_OUTPUT_BYTES = 60 * 1024
MAX_ENTRIES = 100
MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = "2025-03-26"


def catalog_spec():
    specs = [
        ("fs_list", "filesystem", "List one directory inside the configured workspace, without recursion.",
         [], ["filesystem_entries"], {"path": "$input.directory"}, {"filesystem_entries": "files"}),
        ("fs_read", "filesystem", "Read a UTF-8 file from a workspace directory listing, capped at 48 KiB.",
         ["filesystem_entries"], ["file_content"], {"path": "$steps.0.result.files.0.path"}, {"file_content": "content"}),
        ("git_status", "git", "Read the working tree status of a Git repository inside the configured workspace.",
         [], ["git_repository"], {"path": "$input.repository"}, {"git_repository": "repo"}),
        ("git_diff", "git", "Read the unstaged working tree diff of a discovered Git repository. No external diff programs.",
         ["git_repository"], ["git_diff"], {"path": "$steps.0.result.repo"}, {"git_diff": "diff"}),
    ]
    return [{
        "key": key, "name": key, "server": server, "version": "1", "risk": "read",
        "description": f"{description} [consumes: {','.join(consumes)} | produces: {','.join(produces)}]",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096,
                     "description": "Path relative to SWITCHBOARD_WORKSPACE; use . for its root."}},
            "required": ["path"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "consumes": consumes, "produces": produces, "args": args, "outputs": outputs,
    } for key, server, description, consumes, produces, args, outputs in specs]


class LocalTools:
    def __init__(self, kind, workspace=None):
        if kind not in {"filesystem", "git"}:
            raise ValueError("Unknown local adapter kind")
        configured = workspace if workspace is not None else os.getenv("SWITCHBOARD_WORKSPACE")
        if not configured or not Path(configured).is_absolute():
            raise ValueError("Set SWITCHBOARD_WORKSPACE to an explicit absolute directory")
        self.root = Path(configured).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("SWITCHBOARD_WORKSPACE must be a directory")
        self.kind = kind
        self.tools = {tool["name"]: tool for tool in catalog_spec() if tool["server"] == kind}

    def _within(self, path):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path escapes the configured workspace")
        return resolved

    def _path(self, value):
        if "\x00" in value:
            raise ValueError("Path contains a null byte")
        return self._within(self.root / value)

    def _relative(self, path):
        return path.relative_to(self.root).as_posix()

    def list_directory(self, path):
        directory = self._path(path)
        if not directory.is_dir():
            raise ValueError("Path must identify a directory")
        entries, files = [], []
        omitted = 0
        truncated = False
        # Bound both directory scanning and the response. Names are sorted within
        # the retained slice; callers are told explicitly when it is incomplete.
        with os.scandir(directory) as stream:
            for index, entry in enumerate(stream):
                if index >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    resolved = self._within(Path(entry.path))
                except (OSError, ValueError, RuntimeError):
                    omitted += 1
                    continue
                if resolved.is_file():
                    item = {"name": entry.name, "path": self._relative(resolved),
                            "kind": "file", "bytes": resolved.stat().st_size}
                    files.append(item)
                elif resolved.is_dir():
                    item = {"name": entry.name, "path": self._relative(resolved), "kind": "directory"}
                else:
                    continue
                entries.append(item)
        return {"directory": self._relative(directory),
                "entries": sorted(entries, key=lambda item: item["name"]),
                "files": sorted(files, key=lambda item: item["name"]),
                "truncated": truncated, "omitted_unsafe_links": omitted}

    def read_file(self, path):
        target = self._path(path)
        if not target.is_file():
            raise ValueError("Path must identify a regular file")
        with target.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the 48 KiB read limit")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("File is not UTF-8 text") from exc
        return {"path": self._relative(target), "content": content, "bytes": len(data)}

    def _repository(self, path):
        target = self._path(path)
        if not target.is_dir():
            raise ValueError("Repository path must identify a directory")
        # Do not let Git discover a repository above the configured root.
        current = target
        while True:
            marker = current / ".git"
            if marker.exists() or marker.is_symlink():
                marker = self._within(marker)
                if marker.is_file():
                    with marker.open("rb") as stream:
                        header = stream.read(4097)
                    if len(header) > 4096 or not header.startswith(b"gitdir: "):
                        raise ValueError("Unsupported .git indirection")
                    gitdir = self._within(current / header[8:].decode("utf-8").strip())
                else:
                    gitdir = marker
                if not gitdir.is_dir():
                    raise ValueError("Git metadata must be a directory inside the workspace")
                common = gitdir / "commondir"
                if common.exists():
                    common = self._within(common)
                    with common.open("rb") as stream:
                        content = stream.read(4097)
                    if len(content) > 4096:
                        raise ValueError("Invalid Git common directory")
                    self._within(gitdir / content.decode("utf-8").strip())
                return current
            if current == self.root:
                raise ValueError("No Git repository exists inside the configured workspace at this path")
            current = current.parent

    @staticmethod
    def git_environment():
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                            "GIT_PAGER": "cat"})
        return environment

    def _git(self, repository, operation):
        operations = {
            "status": ["status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=all"],
            "diff": ["diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--"],
        }
        argv = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
                "--work-tree=" + str(repository), "-C", str(repository)]
        # Git's clean/process filters can run even during a diff. Enumerate
        # effective filter settings and disable them before reading the tree.
        configured_filters = self._git_output(
            [*argv, "config", "--name-only", "--null", "--includes", "--get-regexp", r"^filter\..*\."],
            absent_ok=True,
        )
        filter_names = set()
        for record in configured_filters.split("\0"):
            if record:
                filter_names.add(record[len("filter."):].rsplit(".", 1)[0])
        for name in sorted(filter_names):
            for setting, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
                argv.extend(["-c", f"filter.{name}.{setting}={value}"])
        return self._git_output([*argv, *operations[operation]])

    def _git_output(self, argv, absent_ok=False):
        # Temporary output avoids an unbounded in-memory capture for large diffs.
        # Git is prevented from refreshing the index; no caller flags or shell.
        with tempfile.TemporaryFile() as stream:
            try:
                process = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, stdout=stream,
                                         stderr=stream, env=self.git_environment(), timeout=5, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Git read exceeded its five-second deadline") from exc
            stream.seek(0)
            data = stream.read(MAX_FILE_BYTES + 1)
        if process.returncode and not (absent_ok and process.returncode == 1 and not data):
            raise ValueError("Git could not read this repository")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Git output exceeds the 48 KiB read limit")
        return data.decode("utf-8", errors="replace")

    def execute(self, name, arguments):
        if not isinstance(name, str) or name not in self.tools:
            raise ValueError("Unknown tool for this adapter")
        try:
            Draft202012Validator(self.tools[name]["inputSchema"]).validate(arguments)
        except ValidationError as exc:
            raise ValueError("Invalid arguments: supply only the required path string") from exc
        if name == "fs_list":
            result = self.list_directory(arguments["path"])
        elif name == "fs_read":
            result = self.read_file(arguments["path"])
        else:
            repository = self._repository(arguments["path"])
            operation = "status" if name == "git_status" else "diff"
            output = self._git(repository, operation)
            result = {"repo": self._relative(repository), operation: output}
            if operation == "status":
                result["clean"] = not bool(output.strip())
            else:
                result["scope"] = "unstaged working tree"
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_OUTPUT_BYTES:
            raise ValueError("Encoded output exceeds the adapter response limit")
        return result

    def dispatch(self, message):
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Request params must be an object")
        if method == "initialize":
            version = params.get("protocolVersion", PROTOCOL_VERSION)
            return {"protocolVersion": version if isinstance(version, str) and version in {PROTOCOL_VERSION, "2025-06-18", "2025-11-25"} else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": f"switchboard-local-{self.kind}", "version": "1.0.0"}}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{field: tool[field] for field in ("name", "description", "inputSchema", "annotations")}
                              for tool in self.tools.values()]}
        if method == "tools/call":
            try:
                result = self.execute(params.get("name"), params.get("arguments", {}))
                return {"content": [{"type": "text", "text": json.dumps(result)}],
                        "structuredContent": result, "isError": False}
            except (ValueError, OSError, RuntimeError) as exc:
                return {"content": [{"type": "text", "text": str(exc)[:500]}], "isError": True}
        raise KeyError("Method not found")


def serve(adapter):
    while line := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
        if len(line) > MAX_MESSAGE_BYTES:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32600, "message": "Request exceeds message limit"}}), flush=True)
            return
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            elif "id" not in message:
                continue
            else:
                response = {"jsonrpc": "2.0", "id": message["id"]}
                try:
                    response["result"] = adapter.dispatch(message)
                except (ValueError, KeyError) as exc:
                    response["error"] = {"code": -32601 if isinstance(exc, KeyError) else -32602,
                                         "message": str(exc)}
        print(json.dumps(response), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=["filesystem", "git"])
    args = parser.parse_args()
    try:
        adapter = LocalTools(args.kind)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Local adapter configuration error: {exc}\n")
    serve(adapter)


if __name__ == "__main__":
    main()

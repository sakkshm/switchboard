import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.transport import MCPTransport, TransportError
from catalog.local_server import MAX_FILE_BYTES, LocalTools, catalog_spec
from scripts.seed import validate_live

ROOT = Path(__file__).resolve().parents[1]


def test_local_adapter_requires_an_explicit_absolute_workspace(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_WORKSPACE", raising=False)
    with pytest.raises(ValueError, match="explicit absolute"):
        LocalTools("filesystem")
    with pytest.raises(ValueError, match="explicit absolute"):
        LocalTools("filesystem", ".")


def test_filesystem_listing_read_and_path_confinement(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n", encoding="utf-8", newline="\n")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    adapter = LocalTools("filesystem", workspace)
    listing = adapter.execute("fs_list", {"path": "."})
    assert listing["files"][0]["path"] == "hello.txt"
    assert adapter.execute("fs_read", {"path": listing["files"][0]["path"]})["content"] == "hello\n"
    for path in ("../outside.txt", str(tmp_path / "outside.txt")):
        with pytest.raises(ValueError, match="escapes"):
            adapter.execute("fs_read", {"path": path})
    with pytest.raises(ValueError, match="Invalid arguments"):
        adapter.execute("fs_read", {"path": "hello.txt", "command": "ignored"})
    (workspace / "large.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="48 KiB"):
        adapter.execute("fs_read", {"path": "large.txt"})
    (workspace / "binary.bin").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        adapter.execute("fs_read", {"path": "binary.bin"})


def test_filesystem_symlinks_cannot_escape_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    outside_directory = tmp_path / "external"
    outside_directory.mkdir()
    (outside_directory / "file.txt").write_text("outside", encoding="utf-8")
    try:
        (workspace / "link.txt").symlink_to(outside)
        (workspace / "external").symlink_to(outside_directory, target_is_directory=True)
    except OSError:
        pytest.skip("This account cannot create filesystem symlinks")
    adapter = LocalTools("filesystem", workspace)
    with pytest.raises(ValueError, match="escapes"):
        adapter.execute("fs_read", {"path": "link.txt"})
    with pytest.raises(ValueError, match="escapes"):
        adapter.execute("fs_read", {"path": "external/file.txt"})
    listing = adapter.execute("fs_list", {"path": "."})
    assert listing["files"] == []
    assert listing["omitted_unsafe_links"] == 2


@pytest.fixture
def repository(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git is not installed")
    root = tmp_path / "repo"
    root.mkdir()
    environment = LocalTools.git_environment()
    subprocess.run(["git", "-c", "init.templateDir=", "init", str(root)], check=True,
                   env=environment, capture_output=True)
    (root / "sample.txt").write_text("before\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(root), "add", "sample.txt"], check=True,
                   env=environment, capture_output=True)
    (root / "sample.txt").write_text("after\n", encoding="utf-8", newline="\n")
    return root


def test_git_status_diff_are_real_read_only_and_do_not_accept_flags(repository):
    adapter = LocalTools("git", repository)
    before_index = (repository / ".git/index").read_bytes()
    status = adapter.execute("git_status", {"path": "."})
    assert "sample.txt" in status["status"]
    assert status["repo"] == "."
    diff = adapter.execute("git_diff", {"path": status["repo"]})
    assert "+after" in diff["diff"] and "-before" in diff["diff"]
    assert (repository / ".git/index").read_bytes() == before_index
    with pytest.raises(ValueError, match="Invalid arguments"):
        adapter.execute("git_diff", {"path": ".", "args": ["--external-diff"]})
    with pytest.raises(ValueError, match="escapes"):
        adapter.execute("git_status", {"path": ".."})


def test_git_cannot_discover_a_repository_above_workspace(repository):
    child = repository / "child"
    child.mkdir()
    adapter = LocalTools("git", child)
    with pytest.raises(ValueError, match="No Git repository"):
        adapter.execute("git_status", {"path": "."})


def test_git_rejects_metadata_pointing_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-git"
    outside.mkdir()
    (workspace / ".git").write_text("gitdir: ../outside-git\n", encoding="utf-8")
    adapter = LocalTools("git", workspace)
    with pytest.raises(ValueError, match="escapes"):
        adapter.execute("git_status", {"path": "."})


def test_untrusted_git_environment_cannot_override_the_workspace(repository, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(repository.parent / "wrong"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repository.parent))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "untrusted-program")
    assert "sample.txt" in LocalTools("git", repository).execute("git_status", {"path": "."})["status"]


def test_git_local_settings_cannot_redirect_the_worktree_or_run_filters(repository):
    config = repository / ".git/config"
    with config.open("a", encoding="utf-8") as stream:
        stream.write('\n[core]\n\tworktree = ..\n[filter "dangerous"]\n'
                     '\tclean = exit 79\n\tprocess = exit 79\n\trequired = true\n')
    (repository / ".gitattributes").write_text("sample.txt filter=dangerous\n", encoding="utf-8")
    diff = LocalTools("git", repository).execute("git_diff", {"path": "."})
    assert "+after" in diff["diff"] and "-before" in diff["diff"]


def test_malformed_tool_name_returns_a_tool_error(tmp_path):
    adapter = LocalTools("filesystem", tmp_path)
    assert adapter.dispatch({"method": "tools/call", "params": {"name": []}})["isError"]


def test_both_adapters_roundtrip_through_actual_mcp_stdio(repository):
    async def run():
        servers = {kind: {"command": "{python}", "args": ["-m", "catalog.local_server", "--kind", kind],
                          "cwd": "{root}", "env": {"SWITCHBOARD_WORKSPACE": str(repository)}}
                   for kind in ("filesystem", "git")}
        transport = MCPTransport(servers=servers)
        for kind in servers:
            tools = await transport.list_tools(kind)
            assert len(tools) == 2
            assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
            assert len(validate_live(catalog_spec(), tools, kind)) == 2
        metadata = {tool["key"]: tool for tool in catalog_spec()}
        file_result = await transport.call(metadata["fs_read"], {"path": "sample.txt"})
        assert file_result["content"] == "after\n"
        diff_result = await transport.call(metadata["git_diff"], {"path": "."})
        assert "+after" in diff_result["diff"]
    asyncio.run(run())


def test_sample_metadata_and_disabled_defaults():
    sample = json.loads((ROOT / "catalog/local_catalog.json").read_text(encoding="utf-8"))
    assert sample == catalog_spec()
    transport = MCPTransport()
    for server in ("filesystem", "git"):
        with pytest.raises(TransportError, match="allowlist"):
            transport._config(server)

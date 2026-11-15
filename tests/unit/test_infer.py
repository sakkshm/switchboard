from types import SimpleNamespace

from switchboard import index, infer

TOOLS = [
    {
        "key": "fs.read",
        "description": "Read a file.",
        "input_schema": '{"required": ["path"], "properties": {"path": {}, "tail": {}}}',
        "output_schema": '{"properties": {"content": {}}}',
    },
    {
        "key": "fs.write",
        "description": "Write a file.",
        "input_schema": '{"required": ["path", "content"], "properties": {"path": {}, "content": {}}}',
        "output_schema": '{"properties": {"content": {}}}',
    },
    {
        "key": "web.fetch",
        "description": "Fetch a page.",
        "input_schema": '{"required": ["url"], "properties": {"url": {}, "timeout_ms": {}}}',
        "output_schema": "{}",
    },
]

REPLY = (
    '```json\n'
    '{"fs.read": {"produces": ["content"]},'
    ' "fs.write": {"produces": []},'
    ' "web.fetch": {"produces": ["content", "nonsense"]}}\n'
    '```'
)


def test_schema_props_falls_back_to_all_properties():
    assert infer.schema_props('{"properties": {"a": {}, "b": {}}}') == (["a", "b"], ["a", "b"])
    assert infer.schema_props("not json") == ([], [])


def test_infer_uses_required_inputs_and_drops_unmatched_types():
    result = infer.infer(TOOLS, ask_model=lambda _prompt: REPLY)
    assert result["web.fetch"]["consumes"] == ["url"]  # optional timeout_ms is not bound
    assert result["fs.write"]["consumes"] == ["path", "content"]
    assert result["web.fetch"]["produces"] == ["content"]  # "nonsense" is in no input list
    assert result["fs.write"]["produces"] == []


def test_infer_rejects_a_non_json_reply():
    import pytest

    with pytest.raises(ValueError, match="did not return JSON"):
        infer.infer(TOOLS, ask_model=lambda _prompt: "sorry, I cannot help")


def test_infer_tolerates_a_malformed_entry():
    reply = '{"fs.read": ["content"], "web.fetch": {"produces": ["content"]}}'
    result = infer.infer(TOOLS, ask_model=lambda _prompt: reply)
    assert "fs.read" not in {key for key, value in result.items() if value["produces"]}
    assert result["web.fetch"]["produces"] == ["content"]


def test_capability_precedence_header_manual_inferred():
    tool = SimpleNamespace(
        description="no header",
        input_schema={"required": ["path"]},
        output_schema={"properties": {"content": {}}},
    )
    inferred = {"consumes": ["content"], "produces": ["file"]}
    assert index.capabilities(tool, None, inferred) == (["content"], ["file"])
    assert index.capabilities(tool, {"consumes": ["path"]}, inferred) == (["path"], [])
    tool.description = "Header. [consumes: a | produces: b]"
    assert index.capabilities(tool, {"consumes": ["path"]}, inferred) == (["a"], ["b"])

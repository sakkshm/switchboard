import pytest

from switchboard import binder, index, planner, validator

TOOLS = {
    "echo.echo_a": {
        "consumes": ["value"],
        "produces": ["echoed"],
        "output_schema": '{"properties": {"echoed": {}}}',
    },
    "echo.echo_b": {
        "consumes": ["echoed"],
        "produces": ["playback"],
        "output_schema": '{"properties": {"playback": {}}}',
    },
}


def test_capability_header():
    assert index.parse_capabilities("Echo. [consumes: a, b | produces: c]") == (
        ["a", "b"],
        ["c"],
    )
    assert index.parse_capabilities("no header") == ([], [])


def test_bind_and_validate():
    steps, required = binder.bind(TOOLS, ["echo.echo_a", "echo.echo_b"])
    assert required == ["value"]
    assert steps[1]["args"] == {"echoed": "$steps.0.result.echoed"}
    assert validator.check(steps, TOOLS) is None


def test_reject_future_binding():
    steps = [
        {"i": 0, "tool": "echo.echo_a", "args": {"value": "$input.value"}},
        {"i": 1, "tool": "echo.echo_b", "args": {"echoed": "$steps.5.result.echoed"}},
    ]
    assert "not earlier" in validator.check(steps, TOOLS)


def test_resolve_rejects_unknown_path():
    with pytest.raises(KeyError):
        binder.resolve("$steps.0.result.missing", {}, [{"echoed": "HI"}])


def test_empty_graph_has_no_plan():
    class EmptyHydra:
        def query(self, *_args, **_kwargs):
            return []

    with pytest.raises(planner.NoPlan):
        planner.make(EmptyHydra(), "anything")

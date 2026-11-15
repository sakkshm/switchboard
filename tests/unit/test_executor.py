from types import SimpleNamespace

from switchboard import executor


def test_scalar_result_unwrapped():
    wrapped = SimpleNamespace(structured_content={"result": "HELLO"})
    assert executor.value(wrapped) == "HELLO"

    structured = SimpleNamespace(structured_content={"echoed": "HELLO"})
    assert executor.value(structured) == {"echoed": "HELLO"}


def test_go_file_read_unwraps_resource_text():
    # GitHub's server returns {"content": [status text, resource]} for file reads
    blocks = [
        {"type": "text", "text": "successfully downloaded text file"},
        {"type": "resource", "resource": {"text": "FILE BODY"}},
    ]
    result = SimpleNamespace(structured_content={"content": blocks})
    assert executor.value(result) == "FILE BODY"

    status_only = SimpleNamespace(structured_content={"content": [{"type": "text", "text": "ok"}]})
    assert executor.value(status_only) == "ok"

    # any other single-key object is passed through untouched
    other = SimpleNamespace(structured_content={"items": [1, 2]})
    assert executor.value(other) == {"items": [1, 2]}

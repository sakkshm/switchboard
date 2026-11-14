from types import SimpleNamespace

from switchboard import executor


def test_scalar_result_unwrapped():
    wrapped = SimpleNamespace(structured_content={"result": "HELLO"})
    assert executor.value(wrapped) == "HELLO"

    structured = SimpleNamespace(structured_content={"echoed": "HELLO"})
    assert executor.value(structured) == {"echoed": "HELLO"}

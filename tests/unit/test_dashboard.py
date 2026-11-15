from switchboard import dashboard


def test_load_json_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RESULTS", tmp_path)
    assert dashboard.load_json("latest.json") == {}


def test_snapshot_reports_down_gracefully(monkeypatch):
    class Broken:
        url = "http://127.0.0.1:1"

        def scalar(self, *args, **kwargs):
            raise RuntimeError("boom")

        def query(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(dashboard, "Hydra", type("H", (), {"from_config": staticmethod(lambda c: Broken())}))
    monkeypatch.setattr(dashboard, "config", type("C", (), {"load": staticmethod(lambda: {"servers": {}})}))
    got = dashboard.snapshot()
    assert got["ready"] is False

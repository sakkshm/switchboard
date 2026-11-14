"""HydraDB OSS HTTP client with a typed-cell decoder."""

import hashlib
import os

import requests

DEFAULT_TOKEN = "local-development-token-32-bytes"


def decode(value):
    """Decode row cells (`{"type","value"}`) and path-tagged values (`{"String": ...}`)."""
    if isinstance(value, dict):
        if value.get("type") == "null" and set(value) <= {"type", "value"}:
            return None
        if set(value) == {"type", "value"}:
            return decode(value["value"])
        if len(value) == 1:
            tag, inner = next(iter(value.items()))
            if tag and tag[0].isupper():
                return decode(inner)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def node_id(key):
    """Stable integer id for a `server.tool` key. OSS merges on integer ids."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:5], "big")


class Hydra:
    def __init__(self, url=None, token=None, graph=None, cell_id="cell-0"):
        self.url = (url or os.getenv("HYDRADB_URL", "http://127.0.0.1:18443")).rstrip("/")
        self.token = token or os.getenv("HYDRADB_TOKEN", DEFAULT_TOKEN)
        self.graph = graph or os.getenv("HYDRADB_GRAPH", "default")
        self.cell_id = cell_id

    @classmethod
    def from_config(cls, config):
        settings = config.get("hydradb", {})
        token = os.getenv(settings.get("token_env", "HYDRADB_TOKEN"), "")
        return cls(
            url=settings.get("url"),
            token=token or None,
            graph=settings.get("graph"),
        )

    def query(self, cypher, parameters=None):
        body = {"cell_id": self.cell_id, "query": cypher}
        if parameters is not None:
            body["parameters"] = parameters
        response = requests.post(
            f"{self.url}/v1/graphs/{self.graph}/query",
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Graph-Namespace": self.graph,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
        return [[decode(cell) for cell in row] for row in response.json().get("rows", [])]

    def scalar(self, cypher, parameters=None):
        rows = self.query(cypher, parameters)
        return rows[0][0] if rows else None

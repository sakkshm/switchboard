"""Synthetic MCP catalog and trusted capability metadata."""

import json
from pathlib import Path


def load_catalog(path=None):
    source = Path(path) if path else Path(__file__).with_name("catalog.json")
    return json.loads(source.read_text(encoding="utf-8"))

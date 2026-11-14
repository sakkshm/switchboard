"""Upstream server config: connection data only, never a tool catalog."""

import copy
import json
import os
import re
from pathlib import Path

CONFIG_PATH = Path(
    os.getenv("SWITCHBOARD_CONFIG", "~/.config/switchboard/config.json")
).expanduser()

DEFAULTS = {
    "servers": {},
    "hydradb": {
        "url": "http://127.0.0.1:18443",
        "graph": "default",
        "token_env": "HYDRADB_TOKEN",
    },
}

ENV_REF = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


def load():
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULTS)
    config = json.loads(CONFIG_PATH.read_text())
    for key, value in DEFAULTS.items():
        config.setdefault(key, copy.deepcopy(value))
    return config


def save(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def resolve(value):
    """Expand `{env:NAME}`. Anything else passes through unchanged."""
    if isinstance(value, str):
        match = ENV_REF.match(value)
        return os.environ.get(match.group(1), "") if match else value
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    return value

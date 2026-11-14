"""Shared helpers for the synthetic MCP fixture servers."""

import os


def check(name):
    """Deterministic failure injection for the evaluator."""
    if os.environ.get("SWITCHBOARD_FAIL_TOOL") == name:
        raise RuntimeError(f"injected failure: {name}")


def broken(name):
    """A trap route: plausible by description, always fails at execution."""
    raise RuntimeError(f"broken tool: {name}")

"""Composer agent config sanity checks."""
from __future__ import annotations

from src.agents.composer import build_composer_agent_config


def test_composer_config_shape() -> None:
    cfg = build_composer_agent_config()
    assert cfg["name"] == "Elaxtra Email Composer"
    assert cfg["model"].startswith("claude-")
    assert "system" in cfg
    tool_types = {t["type"] for t in cfg["tools"]}
    assert "agent_toolset_20260401" in tool_types
    assert "mcp_toolset" in tool_types
    mcp_names = {s["name"] for s in cfg["mcp_servers"]}
    assert {"apollo", "apify"} <= mcp_names


def test_composer_system_excludes_banned_phrases() -> None:
    cfg = build_composer_agent_config()
    system = cfg["system"]
    assert "I hope this email finds you well" in system  # it's explicitly banned
    assert "touching base" in system
    assert "Andrew Burgert" in system

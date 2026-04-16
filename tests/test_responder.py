"""Responder JSON parsing and config shape."""
from __future__ import annotations

from src.agents.responder import build_responder_agent_config
from src.agents.sessions import _extract_json


def test_responder_config_shape() -> None:
    cfg = build_responder_agent_config()
    assert cfg["name"] == "Elaxtra Reply Responder"
    assert cfg["tools"][0]["type"] == "agent_toolset_20260401"


def test_extract_json_plain() -> None:
    raw = '{"classification": "INTERESTED", "should_reply": true}'
    assert _extract_json(raw) == {"classification": "INTERESTED", "should_reply": True}


def test_extract_json_markdown_fence() -> None:
    raw = '```json\n{"classification": "OPT_OUT"}\n```'
    assert _extract_json(raw) == {"classification": "OPT_OUT"}


def test_extract_json_with_preamble() -> None:
    raw = 'Here is the response:\n{"classification": "QUESTION", "should_reply": true}'
    assert _extract_json(raw) == {"classification": "QUESTION", "should_reply": True}


def test_extract_json_returns_none_on_garbage() -> None:
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None

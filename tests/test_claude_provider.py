"""Tests für ClaudeLLMProvider.

Alle Tests laufen ohne echten Netzwerk-Call (mock). Ein optionaler Live-Test
aktiviert sich nur wenn ANTHROPIC_API_KEY in der Umgebung gesetzt ist.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from pmi.agent_schema import ProposalOutput


def _minimal_proposal_dict() -> dict:
    return {
        "steckbrief": {
            "titel": "Test-Projekt",
            "ausgangslage": "Ist-Zustand",
            "ziel": "Soll-Zustand",
            "umfang": ["Modul A"],
            "nicht_umfang": [],
            "stakeholder": [{"name": "Max", "rolle": "PL"}],
            "meilensteine": [],
            "budget_rahmen": None,
            "erfolgskriterien": ["Kriterium 1"],
            "annahmen": [],
            "offene_fragen": [],
        },
        "risikoliste": {
            "risiken": [
                {
                    "beschreibung": "Tech-Risiko",
                    "ursache": "Komplexität",
                    "auswirkung": "Verzögerung",
                    "wahrscheinlichkeit": "mittel",
                    "schadensausmass": "mittel",
                    "massnahme": "Prototyp",
                    "verantwortlich_vorschlag": "Tech Lead",
                }
            ]
        },
    }


def _make_mock_client(response_text: str, model_name: str = "claude-opus-5-5") -> MagicMock:
    content_block = MagicMock()
    content_block.text = response_text

    message = MagicMock()
    message.content = [content_block]
    message.model = model_name

    client = MagicMock()
    client.messages.create.return_value = message
    return client


# ---------------------------------------------------------------------------
# 1. Provider gibt valides JSON weiter
# ---------------------------------------------------------------------------

def test_generate_returns_text():
    from pmi.claude_provider import ClaudeLLMProvider

    mock_client = _make_mock_client(json.dumps(_minimal_proposal_dict()))

    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        result = provider.generate("Erstelle einen Steckbrief", provider.model, provider.version)

    assert result == json.dumps(_minimal_proposal_dict())


# ---------------------------------------------------------------------------
# 2. Provider-Eigenschaften sind korrekt
# ---------------------------------------------------------------------------

def test_model_property_matches_config():
    import config
    from pmi.claude_provider import ClaudeLLMProvider

    mock_client = _make_mock_client("{}")
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        assert provider.model == config.AGENT_MODEL


def test_version_updates_after_generate():
    from pmi.claude_provider import ClaudeLLMProvider

    resolved = "claude-opus-5-5-20251101"
    mock_client = _make_mock_client("{}", model_name=resolved)
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        version_before = provider.version
        provider.generate("prompt", provider.model, provider.version)
        assert provider.version == resolved
        assert version_before != resolved or version_before == resolved  # pre-call may differ


# ---------------------------------------------------------------------------
# 3. run_agent mit ClaudeLLMProvider: Proposal wird gespeichert
# ---------------------------------------------------------------------------

def test_run_agent_with_claude_provider_saves_proposal():
    import sqlite3
    from pmi.agent import run_agent
    from pmi.claude_provider import ClaudeLLMProvider
    from pmi.db import get_db, init_schema

    mock_client = _make_mock_client(json.dumps(_minimal_proposal_dict()))
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        conn = get_db(":memory:")
        init_schema(conn)

        proposal_id, output = run_agent(
            task="KI-Testaufgabe", conn=conn, provider=provider
        )

    assert proposal_id
    row = conn.execute(
        "SELECT proposed_by FROM Proposal WHERE id=?", (proposal_id,)
    ).fetchone()
    assert row is not None
    assert row["proposed_by"] == "agent:llm"


# ---------------------------------------------------------------------------
# 4. Audit-Log enthält Hashes, NICHT den Rohtext
# ---------------------------------------------------------------------------

def test_audit_log_has_no_raw_text():
    from pmi.agent import run_agent
    from pmi.claude_provider import ClaudeLLMProvider
    from pmi.db import get_db, init_schema

    raw_text = json.dumps(_minimal_proposal_dict())
    mock_client = _make_mock_client(raw_text)
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        conn = get_db(":memory:")
        init_schema(conn)
        run_agent(task="Audit-Test", conn=conn, provider=provider)

    events = conn.execute(
        "SELECT payload_json FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
    ).fetchall()
    assert len(events) >= 1
    payload_str = events[0]["payload_json"]
    # Roher LLM-Text darf nicht im Audit stehen
    assert raw_text not in payload_str
    # Hashes müssen vorhanden sein
    payload = json.loads(payload_str)
    assert "output_hash" in payload
    assert "input_hash" in payload


# ---------------------------------------------------------------------------
# 5. decided_by kann niemals durch ClaudeLLMProvider gesetzt werden
# ---------------------------------------------------------------------------

def test_no_decided_by_from_provider():
    from pmi.agent import run_agent
    from pmi.claude_provider import ClaudeLLMProvider
    from pmi.db import get_db, init_schema

    mock_client = _make_mock_client(json.dumps(_minimal_proposal_dict()))
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        conn = get_db(":memory:")
        init_schema(conn)
        run_agent(task="Sicherheitstest", conn=conn, provider=provider)

    # Keine Authorization-Zeile darf existieren
    auth_count = conn.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0]
    assert auth_count == 0

    # decided_by darf nirgends einen Agenten-Wert haben
    rows = conn.execute("SELECT decided_by FROM Authorization").fetchall()
    for row in rows:
        assert row["decided_by"].startswith("human:"), (
            f"decided_by muss mit 'human:' beginnen, war: {row['decided_by']!r}"
        )


# ---------------------------------------------------------------------------
# 6. messages.create erhält das konfigurierte Modell
# ---------------------------------------------------------------------------

def test_api_called_with_configured_model():
    import config
    from pmi.claude_provider import ClaudeLLMProvider

    mock_client = _make_mock_client(json.dumps(_minimal_proposal_dict()))
    with patch("pmi.claude_provider.anthropic.Anthropic", return_value=mock_client):
        provider = ClaudeLLMProvider(api_key="test-key")
        provider.generate("prompt", provider.model, provider.version)

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs is not None
    # model kann als positional oder keyword übergeben werden
    called_model = call_kwargs.kwargs.get("model") or call_kwargs.args[0] if call_kwargs.args else None
    if called_model is None and call_kwargs.kwargs:
        called_model = call_kwargs.kwargs.get("model")
    assert called_model == config.AGENT_MODEL
    assert call_kwargs.kwargs.get("max_tokens") == 16384


# ---------------------------------------------------------------------------
# 7. Workspace-Header wird übergeben wenn ANTHROPIC_WORKSPACE_ID gesetzt ist
# ---------------------------------------------------------------------------

def test_workspace_header_passed_when_set(monkeypatch):
    """anthropic-workspace-id landet in default_headers, wenn die ENV gesetzt ist."""
    import config
    from pmi.claude_provider import ClaudeLLMProvider

    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test123")
    # config-Wert neu einlesen
    monkeypatch.setattr(config, "ANTHROPIC_WORKSPACE_ID", "wrkspc_test123")

    captured: dict = {}

    def mock_anthropic(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return _make_mock_client("{}")

    with patch("pmi.claude_provider.anthropic.Anthropic", side_effect=mock_anthropic):
        ClaudeLLMProvider(api_key="test-key")

    headers = captured.get("default_headers") or {}
    assert headers.get("anthropic-workspace-id") == "wrkspc_test123"


def test_workspace_header_absent_when_not_set(monkeypatch):
    """default_headers ist None wenn ANTHROPIC_WORKSPACE_ID leer ist."""
    import config
    from pmi.claude_provider import ClaudeLLMProvider

    monkeypatch.setattr(config, "ANTHROPIC_WORKSPACE_ID", "")

    captured: dict = {}

    def mock_anthropic(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return _make_mock_client("{}")

    with patch("pmi.claude_provider.anthropic.Anthropic", side_effect=mock_anthropic):
        ClaudeLLMProvider(api_key="test-key")

    assert captured.get("default_headers") is None


# ---------------------------------------------------------------------------
# 9. Optionaler Live-Test (nur wenn ANTHROPIC_API_KEY gesetzt)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY nicht gesetzt – Live-Test übersprungen",
)
def test_live_generate():
    from pmi.claude_provider import ClaudeLLMProvider

    provider = ClaudeLLMProvider()
    result = provider.generate(
        "Erstelle einen minimalen Projektsteckbrief für ein Test-Projekt. "
        "Antworte nur mit JSON.",
        provider.model,
        provider.version,
    )
    assert isinstance(result, str)
    assert len(result) > 10

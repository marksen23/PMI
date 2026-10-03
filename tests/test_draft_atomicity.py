"""Auftrag 7b Haltepunkt 2 – rote Tests: atomarer/idempotenter draft_proposal.

Echter Kern, echte SQLite. Nur LLM-Provider Fake/Patch.
B3: B1 SIGKILL + B2 Halbstand; C1 offene_fragen; D1 Fake-Härtung.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pmi.agent import FakeLLMProvider
from pmi.approval import approve
from pmi.db import get_db
from pmi.temporal.activities import (
    ApplyWriteInput,
    BeginExecutionInput,
    CreateRequestInput,
    DraftProposalInput,
    activity_apply_write,
    activity_begin_execution,
    activity_create_request,
    activity_draft_proposal,
)

WORKER = Path(__file__).parent / "worker_draft_proposal.py"


def _proposal_dict(*, offene_fragen: list[str] | None = None) -> dict:
    return {
        "steckbrief": {
            "titel": "Atomar-Test",
            "ausgangslage": "Ist",
            "ziel": "Soll",
            "umfang": ["A"],
            "nicht_umfang": [],
            "stakeholder": [{"name": "Ada", "rolle": "PL"}],
            "meilensteine": [],
            "budget_rahmen": None,
            "erfolgskriterien": ["K1"],
            "annahmen": [],
            "offene_fragen": offene_fragen if offene_fragen is not None else ["Frage X?"],
        },
        "risikoliste": {
            "risiken": [
                {
                    "beschreibung": "Risiko",
                    "ursache": "Ursache",
                    "auswirkung": "Auswirkung",
                    "wahrscheinlichkeit": "niedrig",
                    "schadensausmass": "mittel",
                    "massnahme": "Massnahme",
                    "verantwortlich_vorschlag": "PL",
                }
            ]
        },
    }


class CountingFake(FakeLLMProvider):
    def __init__(self, fixed_response: str) -> None:
        super().__init__(fixed_response)
        self.calls = 0

    def generate(self, prompt: str, model: str, version: str) -> str:
        self.calls += 1
        return super().generate(prompt, model, version)


def _patch_counting(provider: CountingFake):
    return patch(
        "pmi.temporal.activities.get_llm_provider",
        return_value=provider,
    )


def _proposal_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Proposal").fetchone()[0]
    conn.close()
    return n


async def _crash_before_draft_audit(
    db_path: Path,
    workflow_id: str,
    provider: CountingFake,
) -> str:
    """Erster Versuch: bricht nach AGENT-Audit vor DRAFT_PROPOSAL ab (Halbstand)."""
    import pmi.agent as agent_mod
    import pmi.temporal.activities as activities_mod

    real_agent_audit = agent_mod.append_audit
    real_activity_audit = activities_mod.append_audit

    def agent_audit(*args, **kwargs):
        return real_agent_audit(*args, **kwargs)

    def activity_audit(conn, dedup_key, event_type, *args, **kwargs):
        if event_type == "DRAFT_PROPOSAL":
            raise RuntimeError("simulated crash before DRAFT_PROPOSAL")
        return real_activity_audit(conn, dedup_key, event_type, *args, **kwargs)

    with _patch_counting(provider):
        with patch.object(agent_mod, "append_audit", side_effect=agent_audit):
            with patch.object(activities_mod, "append_audit", side_effect=activity_audit):
                with pytest.raises(RuntimeError, match="simulated crash"):
                    await activity_draft_proposal(
                        DraftProposalInput(
                            task="Halbstand-Aufgabe",
                            db_path=str(db_path),
                            step=1,
                            workflow_id=workflow_id,
                        )
                    )

    assert provider.calls == 1
    assert _proposal_count(db_path) == 1
    conn = get_db(str(db_path))
    proposal_id = conn.execute("SELECT id FROM Proposal").fetchone()[0]
    n_agent = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
    ).fetchone()[0]
    n_draft = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='DRAFT_PROPOSAL'"
    ).fetchone()[0]
    conn.close()
    assert n_agent == 1
    assert n_draft == 0
    return proposal_id


@pytest.mark.asyncio
async def test_draft_retry_after_partial_writes_no_second_proposal(tmp_db_path):
    """B2: Proposal+AGENT ohne DRAFT → Retry → 1 Proposal, 1 Provider-Call, gleiche id."""
    provider = CountingFake(json.dumps(_proposal_dict()))
    workflow_id = f"a7b-partial-{uuid.uuid4()}"
    first_id = await _crash_before_draft_audit(tmp_db_path, workflow_id, provider)

    with _patch_counting(provider):
        result = await activity_draft_proposal(
            DraftProposalInput(
                task="Halbstand-Aufgabe",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    assert result.proposal_id == first_id
    assert _proposal_count(tmp_db_path) == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_draft_retry_after_agent_audit_before_draft_audit(tmp_db_path):
    """B2: Abbruch zwischen AGENT-Audit und DRAFT-Audit, dann Retry."""
    provider = CountingFake(json.dumps(_proposal_dict()))
    workflow_id = f"a7b-gap-{uuid.uuid4()}"
    first_id = await _crash_before_draft_audit(tmp_db_path, workflow_id, provider)

    with _patch_counting(provider):
        result = await activity_draft_proposal(
            DraftProposalInput(
                task="Halbstand-Aufgabe",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    assert result.proposal_id == first_id
    assert _proposal_count(tmp_db_path) == 1
    assert provider.calls == 1
    conn = get_db(str(tmp_db_path))
    n_draft = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='DRAFT_PROPOSAL'"
    ).fetchone()[0]
    conn.close()
    assert n_draft == 1


def _run_draft_worker(
    db_path: Path,
    workflow_id: str,
    call_count_path: Path,
    *,
    fault_point: str | None,
    fake_json: str,
) -> int:
    env = os.environ.copy()
    env["PMI_AUTHORIZATION_HMAC_SECRET"] = env.get(
        "PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-for-pytest"
    )
    env["PMI_FAKE_LLM_JSON"] = fake_json
    env["PMI_ALLOW_FAKE_LLM"] = "1"
    if fault_point:
        env["PMI_FAULT_INJECT"] = fault_point
    else:
        env.pop("PMI_FAULT_INJECT", None)
    proc = subprocess.run(
        [
            sys.executable,
            str(WORKER),
            str(db_path),
            workflow_id,
            str(call_count_path),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode


@pytest.mark.parametrize(
    "fault_point",
    ["after_draft_proposal_insert", "after_draft_agent_audit"],
)
def test_draft_sigkill_retry_no_second_proposal(tmp_path, fault_point):
    """B1: SIGKILL am Fault-Point, dann Clean-Retry → 1 Proposal, 1 Provider-Call."""
    db_path = tmp_path / f"{fault_point}.db"
    call_count_path = tmp_path / "calls.txt"
    call_count_path.write_text("0", encoding="utf-8")
    workflow_id = f"a7b-kill-{uuid.uuid4()}"
    fake_json = json.dumps(_proposal_dict())

    rc = _run_draft_worker(
        db_path,
        workflow_id,
        call_count_path,
        fault_point=fault_point,
        fake_json=fake_json,
    )
    assert rc == -signal.SIGKILL, (
        f"Worker muss per SIGKILL enden (Fault {fault_point}), rc={rc}"
    )

    rc_clean = _run_draft_worker(
        db_path,
        workflow_id,
        call_count_path,
        fault_point=None,
        fake_json=fake_json,
    )
    assert rc_clean == 0, f"Clean-Retry fehlgeschlagen rc={rc_clean}"

    assert _proposal_count(db_path) == 1
    calls = int(call_count_path.read_text(encoding="utf-8") or "0")
    assert calls == 1, f"Erwartet ein Provider-Aufruf, war {calls}"

    conn = get_db(str(db_path))
    n_draft = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='DRAFT_PROPOSAL'"
    ).fetchone()[0]
    ids = [r[0] for r in conn.execute("SELECT id FROM Proposal").fetchall()]
    conn.close()
    assert n_draft == 1
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_offene_fragen_in_steckbrief_md(tmp_db_path, tmp_git_repo):
    """C1: nicht-leere offene_fragen landen als Abschnitt in steckbrief.md."""
    fragen = ["Was ist der Go-Live-Termin?", "Budget freigegeben?"]
    provider = CountingFake(json.dumps(_proposal_dict(offene_fragen=fragen)))
    workflow_id = f"a7b-of-{uuid.uuid4()}"

    with _patch_counting(provider):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Offene-Fragen-Test",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )
        create = await activity_create_request(
            CreateRequestInput(
                db_path=str(tmp_db_path),
                repo_path=str(tmp_git_repo),
                proposal_id=draft.proposal_id,
                step=3,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    auth = approve(conn, create.request_id, person="alice")
    conn.close()

    execution_id = await activity_begin_execution(
        BeginExecutionInput(
            db_path=str(tmp_db_path),
            repo_path=str(tmp_git_repo),
            request_id=create.request_id,
            auth_id=auth.id,
            idempotency_key=create.request_hash,
            step=6,
            workflow_id=workflow_id,
        )
    )
    await activity_apply_write(
        ApplyWriteInput(
            db_path=str(tmp_db_path),
            repo_path=str(tmp_git_repo),
            payload=draft.payload,
            expected_revision=create.expected_revision,
            idempotency_key=create.request_hash,
            execution_id=execution_id,
            step=7,
            workflow_id=workflow_id,
        )
    )

    md = (tmp_git_repo / "projekte" / draft.proposal_id / "steckbrief.md").read_text(
        encoding="utf-8"
    )
    assert "## Offene Fragen" in md
    for frage in fragen:
        assert frage in md


def test_empty_fake_json_is_unset(monkeypatch):
    """D1: PMI_FAKE_LLM_JSON=\"\" gilt als unset → kein FakeLLMProvider."""
    monkeypatch.setenv("PMI_FAKE_LLM_JSON", "")
    monkeypatch.setenv("PMI_ALLOW_FAKE_LLM", "1")
    from pmi.temporal.activities import get_llm_provider
    from pmi.claude_provider import ClaudeLLMProvider

    with patch("pmi.claude_provider.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value = object()
        provider = get_llm_provider()
    assert isinstance(provider, ClaudeLLMProvider)


def test_fake_requires_allow_outside_pytest(monkeypatch):
    """D1: Fake mit JSON ohne PMI_ALLOW_FAKE_LLM ausserhalb pytest → kein Fake."""
    monkeypatch.setenv("PMI_FAKE_LLM_JSON", json.dumps(_proposal_dict()))
    monkeypatch.delenv("PMI_ALLOW_FAKE_LLM", raising=False)
    # pytest setzt PYTEST_CURRENT_TEST – für „ausserhalb“ entfernen
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from pmi.temporal import activities as activities_mod
    from pmi.claude_provider import ClaudeLLMProvider

    with patch("pmi.claude_provider.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value = object()
        provider = activities_mod.get_llm_provider()
    assert isinstance(provider, ClaudeLLMProvider), (
        "Ohne PMI_ALLOW_FAKE_LLM und ohne pytest darf Fake nicht aktiv sein"
    )

"""Auftrag 7 Haltepunkt 2 – rote Tests: Temporal-Draft → LLM-Steckbrief.

Echte SQLite-DB und echtes git-Repo. Kern nicht gemockt.
Provider in Tests: FakeLLMProvider via get_llm_provider() (E1).
Kein answers-Signal (A2). Kein Evaluator im Workflow (C1).
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pmi.agent import FakeLLMProvider
from pmi.agent_schema import ProposalOutput
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


def _minimal_proposal_dict() -> dict:
    return {
        "steckbrief": {
            "titel": "Auftrag7-Test",
            "ausgangslage": "Ist",
            "ziel": "Soll",
            "umfang": ["A"],
            "nicht_umfang": [],
            "stakeholder": [{"name": "Ada", "rolle": "PL"}],
            "meilensteine": [],
            "budget_rahmen": None,
            "erfolgskriterien": ["K1"],
            "annahmen": [],
            "offene_fragen": ["Noch offen?"],
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


def _fake_provider() -> FakeLLMProvider:
    return FakeLLMProvider(json.dumps(_minimal_proposal_dict()))


def _commit_count(repo: Path) -> int:
    return int(
        subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], cwd=repo
        ).decode().strip()
    )


def _patch_provider():
    # create=True: Attribut fehlt vor Implementierung; nach HP3 ruft
    # draft_proposal get_llm_provider() auf und dieser Patch greift.
    return patch(
        "pmi.temporal.activities.get_llm_provider",
        create=True,
        return_value=_fake_provider(),
    )


@pytest.mark.asyncio
async def test_draft_uses_run_agent_payload(tmp_db_path):
    """Proposal in DB ist ProposalOutput; proposed_by=agent:llm; kein touch_file."""
    workflow_id = f"a7-draft-{uuid.uuid4()}"
    with _patch_provider():
        result = await activity_draft_proposal(
            DraftProposalInput(
                task="Steckbrief für Auftrag 7",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    row = conn.execute(
        "SELECT proposed_by, payload_json FROM Proposal WHERE id=?",
        (result.proposal_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["proposed_by"] == "agent:llm"
    payload = json.loads(row["payload_json"])
    assert "steckbrief" in payload
    assert "action" not in payload
    assert payload.get("path") != "temporal_output.txt"
    ProposalOutput.model_validate(payload)
    assert "steckbrief" in result.payload
    assert result.payload["steckbrief"]["titel"] == "Auftrag7-Test"


@pytest.mark.asyncio
async def test_draft_dedup_returns_same_proposal(tmp_db_path):
    """Retry derselben step/dedup → dieselbe proposal_id; ein AGENT_PROPOSAL_GENERATED."""
    workflow_id = f"a7-dedup-{uuid.uuid4()}"
    args = DraftProposalInput(
        task="Dedup-Aufgabe",
        db_path=str(tmp_db_path),
        step=1,
        workflow_id=workflow_id,
    )
    with _patch_provider():
        first = await activity_draft_proposal(args)
        second = await activity_draft_proposal(args)

    assert first.proposal_id == second.proposal_id
    assert first.payload == second.payload
    assert "steckbrief" in second.payload

    conn = get_db(str(tmp_db_path))
    n_prop = conn.execute("SELECT COUNT(*) FROM Proposal").fetchone()[0]
    n_agent = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
    ).fetchone()[0]
    n_draft = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='DRAFT_PROPOSAL'"
    ).fetchone()[0]
    conn.close()

    assert n_prop == 1
    assert n_agent == 1
    assert n_draft == 1


@pytest.mark.asyncio
async def test_apply_steckbrief_after_approve(tmp_db_path, tmp_git_repo):
    """FakeLLM-Draft → approve → apply → projekte/<id>/; Status APPLIED."""
    workflow_id = f"a7-apply-{uuid.uuid4()}"
    commits_before = _commit_count(tmp_git_repo)

    with _patch_provider():
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Apply-Steckbrief",
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
    assert execution_id, "begin_execution muss eine execution_id liefern"

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

    project_dir = tmp_git_repo / "projekte" / draft.proposal_id
    assert project_dir.is_dir()
    assert (project_dir / "steckbrief.md").is_file()
    assert (project_dir / "risikoliste.md").is_file()
    assert (project_dir / "proposal.json").is_file()
    assert not (tmp_git_repo / "temporal_output.txt").exists()
    assert _commit_count(tmp_git_repo) == commits_before + 1

    conn = get_db(str(tmp_db_path))
    status = conn.execute(
        "SELECT status FROM Execution WHERE id=?", (execution_id,)
    ).fetchone()["status"]
    conn.close()
    assert status == "APPLIED"


@pytest.mark.asyncio
async def test_llm_creates_no_authorization(tmp_db_path):
    """Draft über run_agent: AGENT_PROPOSAL_GENERATED, Authorization-Count 0."""
    workflow_id = f"a7-noauth-{uuid.uuid4()}"
    with _patch_provider():
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Keine Auth",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    proposed_by = conn.execute(
        "SELECT proposed_by FROM Proposal WHERE id=?", (draft.proposal_id,)
    ).fetchone()["proposed_by"]
    auth_count = conn.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0]
    exec_count = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
    agent_audits = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
    ).fetchone()[0]
    conn.close()
    assert proposed_by == "agent:llm"
    assert agent_audits == 1
    assert auth_count == 0
    assert exec_count == 0


def test_draft_timeout_constants_provisional():
    """D1: PROVISIONAL Timeouts in config.py – 120s / 3 Versuche."""
    import config

    assert config.DRAFT_PROPOSAL_START_TO_CLOSE_SECONDS == 120
    assert config.DRAFT_PROPOSAL_MAXIMUM_ATTEMPTS == 3

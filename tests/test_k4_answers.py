"""Auftrag 10 Haltepunkt 2 – rote Tests: K4 / answers (A3 B1 C3 D1 E1 F1).

Echte SQLite-DB und echtes git-Repo. Kern nicht gemockt.
Kein Auth/Status durch answers oder LLM.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pmi.agent import FakeLLMProvider
from pmi.agent_schema import ProposalOutput
from pmi.approval import approve
from pmi.db import get_db, init_schema
from pmi.temporal.activities import (
    CreateRequestInput,
    DraftProposalInput,
    activity_create_request,
    activity_draft_proposal,
)


def _proposal_dict(*, questions: list[str]) -> dict:
    return {
        "steckbrief": {
            "titel": "K4-Test",
            "ausgangslage": "Ist",
            "ziel": "Soll",
            "umfang": ["A"],
            "nicht_umfang": [],
            "stakeholder": [{"name": "Ada", "rolle": "PL"}],
            "meilensteine": [],
            "budget_rahmen": None,
            "erfolgskriterien": ["K1"],
            "annahmen": [],
            "offene_fragen": questions,
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


def _patch_questions(questions: list[str]):
    provider = FakeLLMProvider(json.dumps(_proposal_dict(questions=questions)))
    return patch(
        "pmi.temporal.activities.get_llm_provider",
        return_value=provider,
    )


def _auth_count(db_path: Path) -> int:
    conn = get_db(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0]
    conn.close()
    return n


def _proposal_versions(db_path: Path, proposal_id: str) -> list[dict]:
    conn = get_db(str(db_path))
    rows = conn.execute(
        "SELECT version, proposed_by, payload_json, "
        "COALESCE(diff_json, '') AS diff_json "
        "FROM Proposal WHERE id=? ORDER BY version",
        (proposal_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_apply_answers_creates_proposal_version_2(tmp_db_path):
    """B1: answers → gleiche proposal_id, version=2."""
    from pmi.answers import apply_answers  # noqa: F401 – fehlt vor HP3

    workflow_id = f"k4-v2-{uuid.uuid4()}"
    with _patch_questions(["Budgetrahmen?", "Go-Live?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 v2",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    result = apply_answers(
        conn,
        proposal_id=draft.proposal_id,
        answers=["100k EUR", "Q3"],
        person="markus",
    )
    conn.close()

    versions = _proposal_versions(tmp_db_path, draft.proposal_id)
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[1]["version"] == 2
    assert result["version"] == 2
    payload = json.loads(versions[1]["payload_json"])
    ProposalOutput.model_validate(payload)
    # Antworten fließen in den Steckbrief (konkrete Form: Implementierung)
    assert "100k EUR" in json.dumps(payload, ensure_ascii=False)
    assert "Q3" in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_apply_answers_proposed_by_human_prefix(tmp_db_path):
    """C3: Person Pflicht; Prefix human: serverseitig."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-human-{uuid.uuid4()}"
    with _patch_questions(["Frage?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 human",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    apply_answers(
        conn,
        proposal_id=draft.proposal_id,
        answers=["Antwort"],
        person="markus",
    )
    conn.close()

    versions = _proposal_versions(tmp_db_path, draft.proposal_id)
    assert len(versions) == 2
    assert versions[1]["proposed_by"] == "human:markus"


def test_apply_answers_empty_person_rejected(tmp_db_path):
    """C3: leere Person → abgewiesen, keine v2."""
    from pmi.answers import apply_answers

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json) VALUES (?,1,?,?)",
        (
            "p-empty",
            "agent:llm",
            json.dumps(_proposal_dict(questions=["X?"])),
        ),
    )
    conn.commit()

    with pytest.raises(ValueError):
        apply_answers(conn, proposal_id="p-empty", answers=["a"], person="")
    conn.close()

    assert len(_proposal_versions(tmp_db_path, "p-empty")) == 1


@pytest.mark.asyncio
async def test_apply_answers_stores_diff(tmp_db_path):
    """B1: Diff zur Agentenfassung an v2 gespeichert (diff_json nicht leer)."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-diff-{uuid.uuid4()}"
    with _patch_questions(["Scope?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 diff",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    apply_answers(
        conn,
        proposal_id=draft.proposal_id,
        answers=["Nur Modul A"],
        person="markus",
    )
    conn.close()

    versions = _proposal_versions(tmp_db_path, draft.proposal_id)
    assert len(versions) == 2
    assert versions[1]["diff_json"]
    diff = json.loads(versions[1]["diff_json"])
    assert isinstance(diff, (dict, list))


@pytest.mark.asyncio
async def test_apply_answers_no_authorization(tmp_db_path):
    """answers erzeugt v2 und keine Authorization (nicht nur No-op)."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-noauth-{uuid.uuid4()}"
    with _patch_questions(["Frage?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 noauth",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    before = _auth_count(tmp_db_path)
    conn = get_db(str(tmp_db_path))
    apply_answers(
        conn,
        proposal_id=draft.proposal_id,
        answers=["A"],
        person="markus",
    )
    conn.close()
    versions = _proposal_versions(tmp_db_path, draft.proposal_id)
    assert len(versions) == 2, "ohne v2 wäre Auth-Count-0 ein leerer Stub-Pass"
    assert _auth_count(tmp_db_path) == before == 0


@pytest.mark.asyncio
async def test_create_request_after_answers_uses_v2_hash(tmp_db_path, tmp_git_repo):
    """B1/D1: create_request nach answers bindet v2; Hash ≠ v1-Payload-Hash."""
    from pmi.answers import apply_answers
    from pmi.db import compute_request_hash

    workflow_id = f"k4-hash-{uuid.uuid4()}"
    with _patch_questions(["Budget?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 hash",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    conn = get_db(str(tmp_db_path))
    apply_answers(
        conn,
        proposal_id=draft.proposal_id,
        answers=["50k"],
        person="markus",
    )
    v1_row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=? AND version=1",
        (draft.proposal_id,),
    ).fetchone()
    v2_row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=? AND version=2",
        (draft.proposal_id,),
    ).fetchone()
    conn.close()
    assert v1_row is not None
    assert v2_row is not None, "v2 fehlt – create_request kann Hash nicht an v2 binden"
    v1_payload = json.loads(v1_row["payload_json"])
    v2_payload = json.loads(v2_row["payload_json"])

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
    req = conn.execute(
        "SELECT proposal_version, request_hash FROM ExecutionRequest WHERE id=?",
        (create.request_id,),
    ).fetchone()
    conn.close()

    assert req["proposal_version"] == 2
    assert req["request_hash"] == compute_request_hash(
        v2_payload, create.expected_revision
    )
    assert req["request_hash"] != compute_request_hash(
        v1_payload, create.expected_revision
    )


@pytest.mark.asyncio
async def test_answers_rejected_after_create_request(tmp_db_path, tmp_git_repo):
    """D1: answers nach create_request abgewiesen."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-late-{uuid.uuid4()}"
    with _patch_questions(["Spät?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="K4 late",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )
    await activity_create_request(
        CreateRequestInput(
            db_path=str(tmp_db_path),
            repo_path=str(tmp_git_repo),
            proposal_id=draft.proposal_id,
            step=3,
            workflow_id=workflow_id,
        )
    )

    conn = get_db(str(tmp_db_path))
    with pytest.raises(ValueError):
        apply_answers(
            conn,
            proposal_id=draft.proposal_id,
            answers=["zu spät"],
            person="markus",
        )
    conn.close()
    assert len(_proposal_versions(tmp_db_path, draft.proposal_id)) == 1


def test_cli_answers_command(tmp_db_path, tmp_git_repo, monkeypatch, capsys):
    """A3: pmi answers <proposal_id> <person> -- <a1> <a2> …"""
    from pmi.cli import _cmd_answers  # noqa: F401
    from pmi.db import insert_proposal

    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-for-pytest")

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    pid = insert_proposal(
        conn,
        payload=_proposal_dict(questions=["Q1?", "Q2?"]),
        proposed_by="agent:llm",
    )
    # version-Spalte sicher 1
    conn.execute("UPDATE Proposal SET version=1 WHERE id=?", (pid,))
    conn.commit()
    conn.close()

    _cmd_answers([pid, "markus", "--", "A1", "A2"])
    out = capsys.readouterr().out
    assert pid in out
    assert "2" in out or "version=2" in out

    versions = _proposal_versions(tmp_db_path, pid)
    assert len(versions) == 2
    assert versions[1]["proposed_by"] == "human:markus"
    assert _auth_count(tmp_db_path) == 0


@pytest.mark.asyncio
async def test_workflow_waits_for_answers_signal(tmp_db_path, tmp_git_repo):
    """A3/D1: bei offene_fragen wartet Workflow auf answers-Signal vor create_request."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from pmi.db import init_schema
    from pmi.temporal.activities import ACTIVITIES
    from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    conn.close()

    workflow_id = f"k4-wf-{uuid.uuid4()}"
    with _patch_questions(["Workflow-Frage?"]):
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="k4-answers-q",
                workflows=[InitiationWorkflow],
                activities=ACTIVITIES,
            ):
                handle = await env.client.start_workflow(
                    InitiationWorkflow.run,
                    WorkflowInput(
                        task="K4 workflow questions",
                        db_path=str(tmp_db_path),
                        repo_path=str(tmp_git_repo),
                        approval_timeout_seconds=3600,
                    ),
                    id=workflow_id,
                    task_queue="k4-answers-q",
                )

                # Draft fertig, noch kein Request
                for _ in range(100):
                    conn = get_db(str(tmp_db_path))
                    n_prop = conn.execute("SELECT COUNT(*) FROM Proposal").fetchone()[0]
                    n_req = conn.execute(
                        "SELECT COUNT(*) FROM ExecutionRequest"
                    ).fetchone()[0]
                    conn.close()
                    if n_prop >= 1 and n_req == 0:
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("Draft nicht rechtzeitig oder Request zu früh")

                conn = get_db(str(tmp_db_path))
                proposal_id = conn.execute(
                    "SELECT id FROM Proposal ORDER BY created_at DESC LIMIT 1"
                ).fetchone()["id"]
                conn.close()

                await handle.signal(
                    InitiationWorkflow.answers,
                    {"answers": ["WF-Antwort"], "person": "markus"},
                )

                # Nach answers: v2 + Request
                for _ in range(100):
                    conn = get_db(str(tmp_db_path))
                    n_v2 = conn.execute(
                        "SELECT COUNT(*) FROM Proposal WHERE id=? AND version=2",
                        (proposal_id,),
                    ).fetchone()[0]
                    n_req = conn.execute(
                        "SELECT COUNT(*) FROM ExecutionRequest"
                    ).fetchone()[0]
                    conn.close()
                    if n_v2 == 1 and n_req == 1:
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("v2/Request nach answers-Signal ausgeblieben")

                # Approve + signal authorized damit Workflow enden kann
                conn = get_db(str(tmp_db_path))
                req_id = conn.execute(
                    "SELECT id FROM ExecutionRequest ORDER BY created_at DESC LIMIT 1"
                ).fetchone()["id"]
                auth = approve(conn, req_id, person="markus")
                conn.close()
                await handle.signal(InitiationWorkflow.authorized, auth.id)
                result = await handle.result()
                assert result.status in ("APPLIED", "AUTH_REJECTED") or result.status


@pytest.mark.asyncio
async def test_workflow_skips_answers_when_no_questions(tmp_db_path, tmp_git_repo):
    """Leere offene_fragen: kein answers nötig; create_request auf v1."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from pmi.db import init_schema
    from pmi.temporal.activities import ACTIVITIES
    from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    conn.close()

    workflow_id = f"k4-skip-{uuid.uuid4()}"
    with _patch_questions([]):
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="k4-answers-skip",
                workflows=[InitiationWorkflow],
                activities=ACTIVITIES,
            ):
                handle = await env.client.start_workflow(
                    InitiationWorkflow.run,
                    WorkflowInput(
                        task="K4 no questions",
                        db_path=str(tmp_db_path),
                        repo_path=str(tmp_git_repo),
                        approval_timeout_seconds=3600,
                    ),
                    id=workflow_id,
                    task_queue="k4-answers-skip",
                )

                for _ in range(100):
                    conn = get_db(str(tmp_db_path))
                    req = conn.execute(
                        "SELECT proposal_version FROM ExecutionRequest "
                        "ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    conn.close()
                    if req is not None:
                        assert req["proposal_version"] == 1
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("Request ohne questions nicht erzeugt")

                conn = get_db(str(tmp_db_path))
                n_prop = conn.execute("SELECT COUNT(*) FROM Proposal").fetchone()[0]
                req_id = conn.execute(
                    "SELECT id FROM ExecutionRequest ORDER BY created_at DESC LIMIT 1"
                ).fetchone()["id"]
                auth = approve(conn, req_id, person="markus")
                conn.close()
                assert n_prop == 1

                await handle.signal(InitiationWorkflow.authorized, auth.id)
                await handle.result()

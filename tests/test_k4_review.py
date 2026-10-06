"""Auftrag 10 Review-Nachzug – rote Tests (Befunde 1–7).

Echte SQLite-DB / echtes git. Kern nicht gemockt.
Scheitern muss an Assertions liegen (Soll-Verhalten), nicht an Import/Setup.
Keine Implementierung in diesem Commit – nur Spec als Tests.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmi.agent import FakeLLMProvider
from pmi.agent_schema import ProposalOutput
from pmi.db import get_db, init_schema, insert_proposal, insert_request
from pmi.temporal.activities import (
    CreateRequestInput,
    DraftProposalInput,
    activity_create_request,
    activity_draft_proposal,
)


def _proposal_dict(*, questions: list[str]) -> dict:
    return {
        "steckbrief": {
            "titel": "K4-Review",
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


def _legacy_schema_with_fk(conn: sqlite3.Connection) -> None:
    """Vorbild ALTER-DB: Proposal PK nur id, ExecutionRequest FK → Proposal(id)."""
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE Proposal (
            id          TEXT PRIMARY KEY,
            version     INTEGER NOT NULL DEFAULT 1,
            proposed_by TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE ExecutionRequest (
            id                    TEXT PRIMARY KEY,
            proposal_id           TEXT NOT NULL,
            proposal_version      INTEGER NOT NULL,
            request_hash          TEXT NOT NULL,
            expected_revision     TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT 'OPEN',
            workflow_id           TEXT,
            created_at            TEXT NOT NULL DEFAULT (datetime('now')),
            consumed_by_execution_id TEXT,
            FOREIGN KEY (proposal_id) REFERENCES Proposal(id)
        );
        CREATE TABLE AuditEvent (
            id           TEXT PRIMARY KEY,
            dedup_key    TEXT UNIQUE,
            event_type   TEXT NOT NULL,
            execution_id TEXT,
            seq          INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def _proposal_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='Proposal'"
    ).fetchone()
    return " ".join((row[0] or "").split()) if row else ""


def _er_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ExecutionRequest'"
    ).fetchone()
    return " ".join((row[0] or "").split()) if row else ""


# ── 1. Migration atomar ──────────────────────────────────────────────────────


def test_migrate_legacy_without_requests_allows_new_request(tmp_path, tmp_git_repo):
    """Befund 1: ALTER-DB ohne Requests → nach Migration insert_request ok, kein FK-Mismatch."""
    db_path = tmp_path / "legacy_empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _legacy_schema_with_fk(conn)
    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json) VALUES (?,?,?,?)",
        ("p-legacy", 1, "agent:llm", json.dumps(_proposal_dict(questions=["Q?"]))),
    )
    conn.commit()
    conn.close()

    conn = get_db(str(db_path))
    init_schema(conn)  # soll atomar migrieren

    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='Proposal__k4'"
    ).fetchone()
    assert leftover is None, "Zwischen-tabelle Proposal__k4 darf nicht liegen bleiben"
    assert "PRIMARY KEY (id, version)" in _proposal_sql(conn)
    # FK muss auf (id, version) zeigen – nicht nur Proposal(id)
    er = _er_sql(conn)
    assert "REFERENCES Proposal(id, version)" in er or "REFERENCES Proposal (id, version)" in er, (
        f"ExecutionRequest-FK falsch nach Migration: {er}"
    )

    req_id = insert_request(
        conn,
        proposal_id="p-legacy",
        proposal_version=1,
        payload=_proposal_dict(questions=["Q?"]),
        expected_revision="deadbeef",
    )
    assert req_id
    conn.close()


def test_migrate_legacy_with_requests_succeeds(tmp_path):
    """Befund 1: ALTER-DB mit Requests → Migration ohne Abbruch, kein Proposal__k4."""
    db_path = tmp_path / "legacy_reqs.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _legacy_schema_with_fk(conn)
    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json) VALUES (?,?,?,?)",
        ("p-req", 1, "agent:llm", json.dumps(_proposal_dict(questions=[]))),
    )
    conn.execute(
        "INSERT INTO ExecutionRequest "
        "(id, proposal_id, proposal_version, request_hash) VALUES (?,?,?,?)",
        ("r-1", "p-req", 1, "hash1"),
    )
    conn.commit()
    conn.close()

    conn = get_db(str(db_path))
    init_schema(conn)  # darf nicht an DROP TABLE Proposal scheitern

    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'Proposal__%'"
    ).fetchall()
    assert leftover == [], f"Resttabellen nach Migration: {leftover}"
    n = conn.execute("SELECT COUNT(*) FROM ExecutionRequest").fetchone()[0]
    assert n == 1
    assert "PRIMARY KEY (id, version)" in _proposal_sql(conn)
    conn.close()


def test_migrate_resume_after_aborted_partial(tmp_path):
    """Befund 1: Wiederanlauf wenn Proposal__k4 von Abbruch liegen blieb."""
    db_path = tmp_path / "legacy_partial.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _legacy_schema_with_fk(conn)
    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json) VALUES (?,?,?,?)",
        ("p-ab", 1, "agent:llm", json.dumps(_proposal_dict(questions=[]))),
    )
    # Simulierter Abbruch: Zieltabelle schon da, alte Proposal noch da
    conn.executescript(
        """
        CREATE TABLE Proposal__k4 (
            id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            proposed_by TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            diff_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (id, version)
        );
        """
    )
    conn.commit()
    conn.close()

    conn = get_db(str(db_path))
    init_schema(conn)  # soll aufräumen und konsistent enden

    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "Proposal" in names
    assert "Proposal__k4" not in names
    assert "PRIMARY KEY (id, version)" in _proposal_sql(conn)
    n = conn.execute("SELECT COUNT(*) FROM Proposal").fetchone()[0]
    assert n == 1
    conn.close()


# ── 2. answers-Wait Timeout → EXPIRED ────────────────────────────────────────


@pytest.mark.asyncio
async def test_answers_wait_timeout_expires(tmp_db_path, tmp_git_repo):
    """Befund 2: wait_condition mit Timeout; ohne Signal → EXPIRED, kein Request."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from pmi.temporal.activities import ACTIVITIES
    from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    conn.close()

    with _patch_questions(["Timeout-Frage?"]):
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="k4-review-timeout",
                workflows=[InitiationWorkflow],
                activities=ACTIVITIES,
            ):
                handle = await env.client.start_workflow(
                    InitiationWorkflow.run,
                    WorkflowInput(
                        task="answers timeout",
                        db_path=str(tmp_db_path),
                        repo_path=str(tmp_git_repo),
                        approval_timeout_seconds=60,
                    ),
                    id=f"k4-to-{uuid.uuid4()}",
                    task_queue="k4-review-timeout",
                )
                try:
                    result = await asyncio.wait_for(handle.result(), timeout=15)
                except Exception as exc:
                    # Ohne wait-Timeout hängt der Workflow bzw. scheitert unspezifisch
                    pytest.fail(
                        f"answers-Wait ohne Timeout – erwartet EXPIRED, bekam {exc!r}"
                    )
                assert result.status == "EXPIRED", (
                    f"ohne answers-Signal erwartet EXPIRED, war {result.status!r}"
                )

    conn = get_db(str(tmp_db_path))
    n_req = conn.execute("SELECT COUNT(*) FROM ExecutionRequest").fetchone()[0]
    conn.close()
    assert n_req == 0, "ohne answers darf kein ExecutionRequest entstehen"


# ── 3. D1 atomar + Signal nach create_request ────────────────────────────────


@pytest.mark.asyncio
async def test_d1_race_answers_vs_create_request(tmp_db_path, tmp_git_repo):
    """Befund 3: Race apply_answers vs create_request – genau eine konsistente Welt."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-race-{uuid.uuid4()}"
    with _patch_questions(["Race?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="race",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_answers() -> None:
        try:
            barrier.wait(timeout=5)
            c = get_db(str(tmp_db_path))
            apply_answers(
                c,
                proposal_id=draft.proposal_id,
                answers=["A"],
                person="markus",
            )
            c.close()
        except BaseException as exc:  # noqa: BLE001 – Race sammeln
            errors.append(exc)

    def do_create() -> None:
        try:
            barrier.wait(timeout=5)
            asyncio.run(
                activity_create_request(
                    CreateRequestInput(
                        db_path=str(tmp_db_path),
                        repo_path=str(tmp_git_repo),
                        proposal_id=draft.proposal_id,
                        step=3,
                        workflow_id=workflow_id,
                    )
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=do_answers)
    t2 = threading.Thread(target=do_create)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    conn = get_db(str(tmp_db_path))
    versions = [
        r[0]
        for r in conn.execute(
            "SELECT version FROM Proposal WHERE id=? ORDER BY version",
            (draft.proposal_id,),
        ).fetchall()
    ]
    reqs = conn.execute(
        "SELECT proposal_version FROM ExecutionRequest WHERE proposal_id=?",
        (draft.proposal_id,),
    ).fetchall()
    conn.close()

    # Erlaubt: (v2 + Request auf v2) ODER (nur v1 + Request auf v1, answers abgewiesen)
    # Verboten: v2 + Request auf v1 (Hash/Version inkonsistent)
    assert len(reqs) == 1
    req_ver = reqs[0][0]
    if 2 in versions:
        assert req_ver == 2, (
            f"Race-Inkonsistenz: Proposal hat v2, Request gebunden an v{req_ver}"
        )
    else:
        assert req_ver == 1
        assert any(isinstance(e, ValueError) for e in errors)


@pytest.mark.asyncio
async def test_signal_after_create_request_rejected_and_audited(
    tmp_db_path, tmp_git_repo
):
    """Befund 3: answers-Signal nach create_request → Ablehnung + Audit, kein stilles Ignorieren."""
    from pmi.answers import apply_answers

    workflow_id = f"k4-late-sig-{uuid.uuid4()}"
    with _patch_questions(["Late?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="late signal",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )
    # answers zuerst (erlaubt), dann create_request
    conn = get_db(str(tmp_db_path))
    apply_answers(
        conn, proposal_id=draft.proposal_id, answers=["ok"], person="markus"
    )
    conn.close()
    await activity_create_request(
        CreateRequestInput(
            db_path=str(tmp_db_path),
            repo_path=str(tmp_git_repo),
            proposal_id=draft.proposal_id,
            step=3,
            workflow_id=workflow_id,
        )
    )

    # Zweiter answers-Versuch nach Request muss abgewiesen + auditiert werden
    conn = get_db(str(tmp_db_path))
    with pytest.raises(ValueError):
        apply_answers(
            conn,
            proposal_id=draft.proposal_id,
            answers=["nochmal"],
            person="markus",
        )
    audits = conn.execute(
        "SELECT event_type FROM AuditEvent WHERE event_type LIKE '%ANSWER%' "
        "OR event_type LIKE '%REJECT%'"
    ).fetchall()
    conn.close()
    types = {r[0] for r in audits}
    assert any(
        "REJECT" in t or "ANSWER" in t for t in types
    ), f"Ablehnung nach create_request muss auditiert sein, war: {types}"
    # Kein v3
    conn = get_db(str(tmp_db_path))
    max_v = conn.execute(
        "SELECT MAX(version) FROM Proposal WHERE id=?", (draft.proposal_id,)
    ).fetchone()[0]
    conn.close()
    assert max_v == 2


# ── 4. CLI signalisiert Workflow; kein v3 auf leeren Fragen ──────────────────


@pytest.mark.asyncio
async def test_cli_answers_signals_waiting_workflow(
    tmp_db_path, tmp_git_repo, monkeypatch
):
    """Befund 4: pmi answers schreibt DB und sendet answers-Signal an wartenden Workflow."""
    from pmi.cli import _cmd_answers

    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))
    monkeypatch.setenv("PMI_TEMPORAL_HOST", "localhost:7233")

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from pmi.temporal.activities import ACTIVITIES
    from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    conn.close()

    signaled: dict = {}

    with _patch_questions(["CLI-Signal?"]):
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="k4-review-cli-sig",
                workflows=[InitiationWorkflow],
                activities=ACTIVITIES,
            ):
                handle = await env.client.start_workflow(
                    InitiationWorkflow.run,
                    WorkflowInput(
                        task="cli signal",
                        db_path=str(tmp_db_path),
                        repo_path=str(tmp_git_repo),
                        approval_timeout_seconds=3600,
                    ),
                    id=f"k4-cli-{uuid.uuid4()}",
                    task_queue="k4-review-cli-sig",
                )

                # Draft fertig, Request noch nicht
                proposal_id = None
                for _ in range(100):
                    conn = get_db(str(tmp_db_path))
                    row = conn.execute(
                        "SELECT id FROM Proposal ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    n_req = conn.execute(
                        "SELECT COUNT(*) FROM ExecutionRequest"
                    ).fetchone()[0]
                    conn.close()
                    if row and n_req == 0:
                        proposal_id = row["id"]
                        break
                    await asyncio.sleep(0.05)
                assert proposal_id is not None

                real_signal = handle.signal

                async def capturing_signal(signal, arg=None, **kwargs):
                    signaled["signal"] = getattr(signal, "__name__", str(signal))
                    signaled["arg"] = arg
                    return await real_signal(signal, arg, **kwargs)

                # CLI soll Client.connect → get_workflow_handle → signal nutzen.
                # Wir patchen connect so, dass derselbe Test-Client/Handle genutzt wird.
                mock_client = MagicMock()
                mock_client.get_workflow_handle = MagicMock(return_value=handle)

                async def fake_connect(host: str):
                    signaled["host"] = host
                    return mock_client

                # handle.signal wrappen
                handle.signal = capturing_signal  # type: ignore[method-assign]

                with patch("temporalio.client.Client.connect", side_effect=fake_connect):
                    # workflow_id muss für CLI auffindbar sein – Dedup aus Draft-Audit
                    _cmd_answers(
                        [proposal_id, "markus", "--", "Antwort-CLI"]
                    )

                # Request muss nach Signal entstehen
                for _ in range(100):
                    conn = get_db(str(tmp_db_path))
                    n_req = conn.execute(
                        "SELECT COUNT(*) FROM ExecutionRequest"
                    ).fetchone()[0]
                    conn.close()
                    if n_req == 1:
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("CLI hat wartenden Workflow nicht weitergeschaltet")

                assert "host" in signaled or signaled.get("arg") is not None, (
                    "CLI muss answers-Signal senden (Client.connect oder signal)"
                )


def test_answers_on_empty_questions_does_not_create_v3(tmp_db_path):
    """Befund 4: Proposal ohne offene_fragen → kein weiteres Versionieren (kein v3)."""
    from pmi.answers import apply_answers

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    pid = insert_proposal(
        conn,
        payload=_proposal_dict(questions=[]),
        proposed_by="agent:llm",
    )
    with pytest.raises(ValueError):
        apply_answers(conn, proposal_id=pid, answers=["x"], person="markus")
    max_v = conn.execute(
        "SELECT MAX(version) FROM Proposal WHERE id=?", (pid,)
    ).fetchone()[0]
    conn.close()
    assert max_v == 1


# ── 5. Person-Namensraum ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "person",
    ["", "   ", "agent:llm", "human:x", "service:bot"],
)
def test_apply_answers_rejects_namespace_person(tmp_db_path, person):
    """Befund 5: whitespace und Präfix agent:/human:/… ablehnen."""
    from pmi.answers import apply_answers

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    pid = insert_proposal(
        conn,
        payload=_proposal_dict(questions=["Q?"]),
        proposed_by="agent:llm",
    )
    with pytest.raises(ValueError):
        apply_answers(conn, proposal_id=pid, answers=["A"], person=person)
    n = conn.execute(
        "SELECT COUNT(*) FROM Proposal WHERE id=? AND version=2", (pid,)
    ).fetchone()[0]
    conn.close()
    assert n == 0


# ── 6. Idempotenz + non-retryable Person ─────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_answers_activity_idempotent_retry(tmp_db_path):
    """Befund 6: Activity-Retry nach Commit → kein zweites v2/v3."""
    from pmi.temporal.activities import (
        ApplyAnswersInput,
        activity_apply_answers,
    )

    workflow_id = f"k4-idem-{uuid.uuid4()}"
    with _patch_questions(["Idem?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="idem",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    args = ApplyAnswersInput(
        db_path=str(tmp_db_path),
        proposal_id=draft.proposal_id,
        answers=["einmal"],
        person="markus",
        step=2,
        workflow_id=workflow_id,
    )
    first = await activity_apply_answers(args)
    second = await activity_apply_answers(args)
    assert first.version == second.version == 2

    conn = get_db(str(tmp_db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM Proposal WHERE id=?", (draft.proposal_id,)
    ).fetchone()[0]
    conn.close()
    assert n == 2, f"erwartet v1+v2, gefunden {n} Zeilen"


@pytest.mark.asyncio
async def test_apply_answers_invalid_person_non_retryable(tmp_db_path):
    """Befund 6: ungültige Person → nicht-retrybarer Activity-Fehler."""
    from temporalio.exceptions import ApplicationError

    from pmi.temporal.activities import (
        ApplyAnswersInput,
        activity_apply_answers,
    )

    workflow_id = f"k4-nr-{uuid.uuid4()}"
    with _patch_questions(["NR?"]):
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="nonretry",
                db_path=str(tmp_db_path),
                step=1,
                workflow_id=workflow_id,
            )
        )

    with pytest.raises(ApplicationError) as ei:
        await activity_apply_answers(
            ApplyAnswersInput(
                db_path=str(tmp_db_path),
                proposal_id=draft.proposal_id,
                answers=["A"],
                person="agent:llm",
                step=2,
                workflow_id=workflow_id,
            )
        )
    assert ei.value.non_retryable is True


# ── 7. Teil-Antworten + ProposalOutput-Validierung ───────────────────────────


def test_merge_keeps_unanswered_questions(tmp_db_path):
    """Befund 7: nur beantwortete Fragen entfernen; Rest bleibt in offene_fragen."""
    from pmi.answers import apply_answers
    from pmi.agent_schema import ProposalOutput

    conn = get_db(str(tmp_db_path))
    init_schema(conn)
    pid = insert_proposal(
        conn,
        payload=_proposal_dict(questions=["Q1?", "Q2?", "Q3?"]),
        proposed_by="agent:llm",
    )
    apply_answers(conn, proposal_id=pid, answers=["nur Q1"], person="markus")
    row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=? AND version=2", (pid,)
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row["payload_json"])
    ProposalOutput.model_validate(payload)
    remaining = payload["steckbrief"]["offene_fragen"]
    assert "Q2?" in remaining
    assert "Q3?" in remaining
    assert "Q1?" not in remaining
    assert "nur Q1" in json.dumps(payload, ensure_ascii=False)

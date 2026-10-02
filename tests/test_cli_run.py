"""
CLI-Tests: pmi run

Nagelt drei Invarianten fest, die vor jeder Änderung an _cmd_run bestehen müssen:

1. ROT  – _cmd_run signalisiert den laufenden InitiationWorkflow über den Temporal-Client
          (ExitCode 0, handle.signal(InitiationWorkflow.authorized, auth_id) aufgerufen,
          auth_id gehört zu dem übergebenen request_id, get_workflow_handle wurde mit der
          workflow_id aus der DB aufgerufen).
2. GRÜN – _cmd_run erzeugt keinen Execution-Datensatz direkt in der DB,
          auch wenn request_id und auth_id gültig sind (Temporal-Pfad, nicht Kern-Pfad).
3. GRÜN – EXPIRED-Request erzeugt keine Execution-Zeile, und _cmd_run startet keinen
          neuen Workflow, wenn der Request abgelaufen ist.
"""
import asyncio
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmi.approval import approve
from pmi.cli import _cmd_run
from pmi.db import get_db, init_schema
from pmi.temporal.activities import (
    CreateRequestInput,
    DraftProposalInput,
    activity_create_request,
    activity_draft_proposal,
)
from pmi.temporal.workflow import InitiationWorkflow


def _execution_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
    conn.close()
    return n


def _setup_open_request(db_path: Path, repo_path: Path) -> tuple[str, str, str]:
    """Legt Proposal + OPEN ExecutionRequest (mit workflow_id via activity) + Authorization an.
    Gibt (request_id, auth_id, workflow_id) zurück.
    workflow_id wird von activity_create_request in die DB geschrieben – nicht per UPDATE."""
    async def _run():
        workflow_id = f"pmi-test-wf-{uuid.uuid4()}"
        draft = await activity_draft_proposal(DraftProposalInput(
            task="cli-test-task",
            db_path=str(db_path),
            step=1,
            workflow_id=workflow_id,
        ))
        create = await activity_create_request(CreateRequestInput(
            db_path=str(db_path),
            repo_path=str(repo_path),
            proposal_id=draft.proposal_id,
            step=3,
            workflow_id=workflow_id,
        ))
        return create.request_id, workflow_id

    request_id, workflow_id = asyncio.run(_run())

    conn = get_db(str(db_path))
    row = conn.execute(
        "SELECT workflow_id FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()
    wf_id_from_db = row["workflow_id"]
    auth = approve(conn, request_id, person="test-approver")
    conn.close()
    return request_id, auth.id, wf_id_from_db


def _setup_expired_request(db_path: Path, repo_path: Path) -> tuple[str, str, str]:
    """Legt EXPIRED ExecutionRequest (workflow_id via activity) + nachträgliche Authorization an.
    Gibt (request_id, auth_id, workflow_id) zurück."""
    async def _run():
        workflow_id = f"pmi-test-expired-wf-{uuid.uuid4()}"
        draft = await activity_draft_proposal(DraftProposalInput(
            task="cli-expired-task",
            db_path=str(db_path),
            step=1,
            workflow_id=workflow_id,
        ))
        create = await activity_create_request(CreateRequestInput(
            db_path=str(db_path),
            repo_path=str(repo_path),
            proposal_id=draft.proposal_id,
            step=3,
            workflow_id=workflow_id,
        ))
        return create.request_id, workflow_id

    request_id, workflow_id = asyncio.run(_run())

    conn = get_db(str(db_path))
    row = conn.execute(
        "SELECT workflow_id FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()
    wf_id_from_db = row["workflow_id"]
    conn.execute(
        "UPDATE ExecutionRequest SET status='EXPIRED' WHERE id=?", (request_id,)
    )
    conn.commit()
    auth = approve(conn, request_id, person="late-approver")
    conn.close()
    return request_id, auth.id, wf_id_from_db


# ── Test 1 (ROT): pmi run signalisiert den richtigen Workflow mit der richtigen auth_id ──

def test_run_signals_workflow_with_correct_auth(tmp_db_path, tmp_git_repo, monkeypatch):
    """
    ROT jetzt: _cmd_run ist Stub → ExitCode 1, kein Signal.
    GRÜN nach Implementierung: ExitCode 0,
      - get_workflow_handle(workflow_id) mit der aus der DB gelesenen workflow_id
      - handle.signal(InitiationWorkflow.authorized, auth_id) mit der auth_id
        aus der DB für request_id.

    Schnittstellenvertrag:
      PMI_DB_PATH       – SQLite-Kontroll-DB
      PMI_REPO_PATH     – git-Repo
      PMI_TEMPORAL_HOST – Temporal-Server (Default "localhost:7233")

    Geprüft:
    - ExitCode == 0
    - get_workflow_handle wurde mit der workflow_id aus der DB aufgerufen
    - handle.signal wurde mit InitiationWorkflow.authorized aufgerufen
    - Signal-Argument ist die auth_id, die in der DB zu request_id gehört
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id, workflow_id = _setup_open_request(tmp_db_path, tmp_git_repo)

    signal_calls: list[dict] = []

    async def fake_signal(signal_fn, *args, **kwargs):
        signal_calls.append({"fn": signal_fn, "args": args, "kwargs": kwargs})

    mock_handle = MagicMock()
    mock_handle.signal = fake_signal

    handle_calls: list[str] = []

    def fake_get_handle(wf_id, *args, **kwargs):
        handle_calls.append(wf_id)
        return mock_handle

    mock_client = MagicMock()
    mock_client.get_workflow_handle = fake_get_handle
    mock_client.start_workflow = AsyncMock()

    async def fake_connect(*a, **kw):
        return mock_client

    exit_code = None
    with patch("temporalio.client.Client.connect", fake_connect):
        try:
            _cmd_run([request_id])
        except SystemExit as e:
            exit_code = e.code

    assert exit_code == 0, (
        f"pmi run muss mit ExitCode 0 enden (war: {exit_code!r}). "
        "Stub gibt aktuell 1 zurück – ROT ist erwartet."
    )
    assert handle_calls and handle_calls[0] == workflow_id, (
        f"get_workflow_handle muss mit workflow_id={workflow_id!r} aus der DB aufgerufen werden, "
        f"war: {handle_calls!r}"
    )
    assert len(signal_calls) >= 1, (
        "handle.signal muss mindestens einmal aufgerufen werden."
    )
    sig = signal_calls[0]
    assert sig["fn"] is InitiationWorkflow.authorized, (
        f"signal muss InitiationWorkflow.authorized übergeben bekommen, war: {sig['fn']!r}"
    )
    assert sig["args"] and sig["args"][0] == auth_id, (
        f"Signal-Argument muss die auth_id={auth_id!r} aus der DB sein "
        f"(für request_id={request_id!r}), war: {sig['args']!r}"
    )


# ── Test 2 (strukturell, GRÜN): kein Execution-Datensatz via direkten Kern-Aufruf ──

def test_run_does_not_create_execution_directly(tmp_db_path, tmp_git_repo, monkeypatch):
    """
    GRÜN jetzt und nach Implementierung.

    _cmd_run darf begin_execution (state_machine) NICHT direkt aufrufen.

    Beweis über Datenbankzustand: request_id und auth_id sind gültig und vollständig —
    ein direkter begin_execution-Aufruf mit diesen Daten würde eine Execution-Zeile
    schreiben. Nach dem CLI-Aufruf muss Execution-Count == 0 sein.

    Fängt die naive Implementierung ab, die Temporal umgeht.
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id, workflow_id = _setup_open_request(tmp_db_path, tmp_git_repo)

    mock_handle = MagicMock()
    mock_handle.signal = AsyncMock()

    mock_client = MagicMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    mock_client.start_workflow = AsyncMock()

    async def fake_connect(*a, **kw):
        return mock_client

    with patch("temporalio.client.Client.connect", fake_connect):
        try:
            _cmd_run([request_id])
        except SystemExit:
            pass

    assert _execution_count(tmp_db_path) == 0, (
        "begin_execution darf nicht direkt aufgerufen worden sein. "
        "Ein Execution-Datensatz wäre entstanden, wenn die CLI den Kern direkt nutzt."
    )


# ── Test 3 (invariant, GRÜN): EXPIRED-Request erzeugt keine Execution, kein neuer Workflow ──

def test_run_expired_request_creates_no_execution(tmp_db_path, tmp_git_repo, monkeypatch):
    """
    GRÜN: pmi run mit EXPIRED-request_id darf keine Execution-Zeile anlegen
    und darf keinen neuen InitiationWorkflow starten.

    Dokumentiert, dass eine nach dem Workflow-Timeout erteilte Freigabe nicht
    an dem Workflow vorbei ausführbar ist – weder durch direkten begin_execution-Aufruf
    noch durch das Starten eines neuen Workflows.

    Ablauf:
    1. ExecutionRequest auf EXPIRED setzen + nachträgliche Authorization anlegen
    2. Temporal-Client mocken (Workflow bereits geschlossen, Signal schlägt fehl)
    3. _cmd_run([request_id]) aufrufen
    4. Keine Execution-Zeile in DB
    5. start_workflow wurde nicht aufgerufen (kein Umgehungsweg über neuen Workflow)
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id, workflow_id = _setup_expired_request(tmp_db_path, tmp_git_repo)

    async def fake_signal_closed(*args, **kwargs):
        raise RuntimeError("Workflow bereits geschlossen (simuliert EXPIRED/done)")

    mock_handle = MagicMock()
    mock_handle.signal = fake_signal_closed

    start_workflow_mock = AsyncMock()

    mock_client = MagicMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    mock_client.start_workflow = start_workflow_mock

    async def fake_connect(*a, **kw):
        return mock_client

    with patch("temporalio.client.Client.connect", fake_connect):
        try:
            _cmd_run([request_id])
        except SystemExit:
            pass

    assert _execution_count(tmp_db_path) == 0, (
        "pmi run darf bei EXPIRED-Request keine Execution-Zeile anlegen."
    )
    start_workflow_mock.assert_not_called()

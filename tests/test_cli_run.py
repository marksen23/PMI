"""
CLI-Tests: pmi run

Nagelt drei Invarianten fest, die vor jeder Änderung an _cmd_run bestehen müssen:

1. ROT  – _cmd_run signalisiert den laufenden InitiationWorkflow über den Temporal-Client
          (ExitCode 0, handle.signal(InitiationWorkflow.authorized, auth_id) aufgerufen,
          auth_id gehört zu dem übergebenen request_id).
2. GRÜN – _cmd_run erzeugt keinen Execution-Datensatz direkt in der DB,
          auch wenn request_id und auth_id gültig sind (Temporal-Pfad, nicht Kern-Pfad).
3. GRÜN – EXPIRED-Request erzeugt keine Execution-Zeile, wenn _cmd_run aufgerufen wird.
"""
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmi.approval import approve
from pmi.cli import _cmd_run
from pmi.db import get_db, init_schema, insert_proposal, insert_request
from pmi.temporal.workflow import InitiationWorkflow


def _execution_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
    conn.close()
    return n


def _setup_open_request(db_path: Path) -> tuple[str, str]:
    """Legt Proposal + OPEN ExecutionRequest + Authorization an.
    Gibt (request_id, auth_id) zurück."""
    conn = get_db(str(db_path))
    init_schema(conn)
    payload = {"action": "touch_file", "path": "run_cli_test.txt", "scope": "local"}
    proposal_id = insert_proposal(conn, payload, "test-agent")
    request_id = insert_request(conn, proposal_id, 1, payload, "")
    auth = approve(conn, request_id, person="test-approver")
    conn.close()
    return request_id, auth.id


def _setup_expired_request(db_path: Path) -> tuple[str, str]:
    """Legt EXPIRED ExecutionRequest + nachträgliche Authorization an.
    Gibt (request_id, auth_id) zurück."""
    conn = get_db(str(db_path))
    init_schema(conn)
    payload = {"action": "touch_file", "path": "expired_cli_test.txt", "scope": "local"}
    proposal_id = insert_proposal(conn, payload, "test-agent")
    request_id = insert_request(conn, proposal_id, 1, payload, "")
    conn.execute("UPDATE ExecutionRequest SET status='EXPIRED' WHERE id=?", (request_id,))
    conn.commit()
    auth = approve(conn, request_id, person="late-approver")
    conn.close()
    return request_id, auth.id


# ── Test 1 (ROT): pmi run signalisiert den Workflow mit der richtigen auth_id ──

def test_run_signals_workflow_with_correct_auth(tmp_db_path, tmp_git_repo, monkeypatch):
    """
    ROT jetzt: _cmd_run ist Stub → ExitCode 1, kein Signal.
    GRÜN nach Implementierung: ExitCode 0, handle.signal(InitiationWorkflow.authorized, auth_id)
    mit der auth_id aus der DB für das übergebene request_id.

    Schnittstellenvertrag für die Implementierung:
      pmi run <request_id>
        PMI_DB_PATH       – Pfad zur SQLite-Kontroll-DB (schon in _get_db() genutzt)
        PMI_REPO_PATH     – Pfad zum git-Repo (Umgebungsvariable)
        PMI_TEMPORAL_HOST – Temporal-Server (Default "localhost:7233")

    Geprüft:
    - ExitCode == 0
    - handle.signal wurde aufgerufen
    - Erstes Argument ist InitiationWorkflow.authorized (nicht eine Activity direkt)
    - Signal-Argument ist die auth_id, die in der DB zu request_id gehört
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id = _setup_open_request(tmp_db_path)

    signal_calls: list[dict] = []

    async def fake_signal(signal_fn, *args, **kwargs):
        signal_calls.append({"fn": signal_fn, "args": args, "kwargs": kwargs})

    mock_handle = MagicMock()
    mock_handle.signal = fake_signal

    mock_client = MagicMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

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
    assert len(signal_calls) >= 1, (
        "handle.signal muss mindestens einmal aufgerufen werden. "
        "pmi run muss den Workflow über Temporal signalisieren, nicht direkt starten."
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

    Kein Eintrag = CLI hat den Kern nicht direkt aufgerufen.
    Fängt die naive Implementierung ab, die Temporal umgeht.
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id = _setup_open_request(tmp_db_path)

    mock_handle = MagicMock()
    mock_handle.signal = AsyncMock()

    mock_client = MagicMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

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


# ── Test 3 (invariant, GRÜN): EXPIRED-Request erzeugt keine Execution via CLI ──

def test_run_expired_request_creates_no_execution(tmp_db_path, tmp_git_repo, monkeypatch):
    """
    GRÜN: pmi run mit EXPIRED-request_id darf keine Execution-Zeile anlegen.

    Dokumentiert, dass eine nach dem Workflow-Timeout erteilte Freigabe nicht
    an dem Workflow vorbei ausführbar ist.

    Ablauf:
    1. ExecutionRequest auf EXPIRED setzen + nachträgliche Authorization anlegen
    2. Temporal-Client mocken (Workflow bereits beendet, Signal schlägt fehl)
    3. _cmd_run([request_id]) aufrufen
    4. Keine Execution-Zeile in DB

    Schutzmechanismen: (a) Der Kernel wirft AuthorizationError("EXPIRED") bei
    einem direkten begin_execution-Aufruf. (b) Ein laufender Workflow nimmt nach
    seinem Timeout kein Signal mehr an.
    """
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))

    request_id, auth_id = _setup_expired_request(tmp_db_path)

    async def fake_signal_closed(*args, **kwargs):
        raise RuntimeError("Workflow bereits geschlossen (simuliert EXPIRED/done)")

    mock_handle = MagicMock()
    mock_handle.signal = fake_signal_closed

    mock_client = MagicMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

    async def fake_connect(*a, **kw):
        return mock_client

    with patch("temporalio.client.Client.connect", fake_connect):
        try:
            _cmd_run([request_id])
        except SystemExit:
            pass

    assert _execution_count(tmp_db_path) == 0, (
        "pmi run darf bei EXPIRED-Request keine Execution-Zeile anlegen. "
        "Weder direkter begin_execution-Aufruf (EXPIRED-Guard im Kernel) "
        "noch ein Workflow-Signal darf eine Execution erzeugen."
    )

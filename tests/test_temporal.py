"""
Temporal-Integrationstests (Haltepunkt 2 – ROT vor Implementierung).

Alle Tests importieren aus pmi.temporal.* (noch nicht vorhanden) und
prüfen den echten DB-Zustand nach dem Workflow-Lauf – nicht nur
den Rückgabewert. Ein Test, der nur den Status vom Workflow entgegennimmt
ohne den DB-Zustand zu prüfen, gilt als nicht bestanden.

Invarianten:
- K1: Keine neue Freigabe im selben Workflow
- K3: Workflow-Ergebnis == Kontroll-DB-Status (DB ist maßgebend, nicht umgekehrt)
- I1: Hash-Bindung; fremde / verbrauchte Authorization → keine Execution
- I3: FAILED nur mit zero_effect_proof
- Dedup: kein doppelter dedup_key in AuditEvent
"""
import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

# Diese Imports scheitern solange pmi.temporal.* nicht existiert → rote Tests.
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
import temporalio.activity as _ta

from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput, WorkflowResult
from pmi.temporal.activities import ACTIVITIES, ApplyWriteInput

from pmi.db import get_db, init_schema, append_audit
from pmi.approval import approve

WORKER_SCRIPT = Path(__file__).parent / "worker_temporal.py"
SIGKILL_RUNS = 50


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _commit_count(repo: Path) -> int:
    return int(
        subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], cwd=repo,
        ).decode().strip()
    )


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo,
    ).decode().strip()


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    return repo


def _execution_status(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _execution_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
    conn.close()
    return n


def _open_executions(db_path: Path) -> list[dict]:
    """Executions in STARTED (terminal = APPLIED, FAILED, ESCALATED)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM Execution WHERE status='STARTED'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dedup_violations(db_path: Path) -> list[str]:
    """dedup_key-Werte die mehr als einmal vorkommen."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT dedup_key, COUNT(*) AS cnt FROM AuditEvent "
        "GROUP BY dedup_key HAVING cnt > 1"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _audit_by_type(db_path: Path, event_type: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM AuditEvent WHERE event_type=? ORDER BY created_at",
        (event_type,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _zero_effect_proof(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT zero_effect_proof FROM Execution ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _auth_used(db_path: Path, auth_id: str) -> int | None:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT used FROM Authorization WHERE id=?", (auth_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


async def _poll_for_request(db_path: Path, timeout: float = 2.0) -> str | None:
    """Wartet bis create_request einen ExecutionRequest in die DB geschrieben hat."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT id FROM ExecutionRequest ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return row[0]
    return None


def _setup_env() -> dict:
    env = os.environ.copy()
    if "PMI_AUTHORIZATION_HMAC_SECRET" not in env:
        env["PMI_AUTHORIZATION_HMAC_SECRET"] = "test-secret-temporal"
    env.pop("PMI_FAULT_INJECT", None)
    return env


# ── Zeitsprung-Tests (start_time_skipping, kein Dev-Server nötig) ────────────


@pytest.mark.asyncio
async def test_wait_timeout_expired(tmp_path, monkeypatch):
    """
    Kein Signal innerhalb der Wartefrist → Workflow endet mit EXPIRED.

    Prüfung (nicht nur Rückgabewert):
    - Keine Execution in DB
    - Commit-Anzahl unverändert
    - AuditEvent vom Typ EXPIRED vorhanden
    - Keine offene (STARTED) Execution
    - Keine doppelten dedup_key
    - Workflow-Ergebnis status == "EXPIRED"
    """
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-temporal")

    db_path = tmp_path / "timeout.db"
    repo = _make_repo(tmp_path, "timeout_repo")
    commits_before = _commit_count(repo)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            result: WorkflowResult = await env.client.execute_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="Zeitsprung-Test",
                    db_path=str(db_path),
                    repo_path=str(repo),
                    approval_timeout_seconds=60,
                ),
                id=f"timeout-{uuid.uuid4()}",
                task_queue="pmi-test",
            )

    # Workflow-Ergebnis
    assert result.status == "EXPIRED", f"Erwartete EXPIRED, erhalten: {result.status}"

    # Kein Execution-Datensatz
    assert _execution_count(db_path) == 0, "Keine Execution-Zeile nach Timeout erwartet"

    # Commit-Anzahl unverändert
    assert _commit_count(repo) == commits_before, "Kein Commit nach Timeout"

    # EXPIRED-Audit vorhanden
    expired = _audit_by_type(db_path, "EXPIRED")
    assert len(expired) >= 1, "EXPIRED-AuditEvent muss vorhanden sein"

    # Keine doppelten dedup_key
    assert _dedup_violations(db_path) == [], "Doppelte dedup_key in AuditEvent"

    # Keine offene Execution
    assert _open_executions(db_path) == [], "Keine STARTED-Execution nach Timeout"

    # ExecutionRequest muss terminal sein (kein zweiter Start möglich)
    conn = sqlite3.connect(str(db_path))
    req = conn.execute(
        "SELECT status FROM ExecutionRequest ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert req is not None and req[0] == "EXPIRED", (
        f"ExecutionRequest muss nach Timeout terminal sein, war: {req}"
    )


@pytest.mark.asyncio
async def test_foreign_authorization_rejected(tmp_path, monkeypatch):
    """
    Signal trägt Authorization-ID, die zu einem anderen Request gehört.

    Prüfung:
    - Keine Execution für den Workflow-Request
    - Commit-Anzahl unverändert
    - Fremde Authorization bleibt unbenutzt (used=0)
    - Keine doppelten dedup_key
    - Workflow-Ergebnis spiegelt Ablehnung wider
    """
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-temporal")

    db_path = tmp_path / "foreign.db"
    repo = _make_repo(tmp_path, "foreign_repo")
    commits_before = _commit_count(repo)

    # Fremde Authorization vor Workflow-Start anlegen (anderer Request).
    conn = get_db(str(db_path))
    init_schema(conn)
    from pmi.db import insert_proposal, insert_request
    foreign_payload = {"action": "touch_file", "path": "foreign.txt", "scope": "local"}
    foreign_prop_id = insert_proposal(conn, foreign_payload, proposed_by="agent:stub")
    foreign_req_id = insert_request(
        conn,
        proposal_id=foreign_prop_id,
        proposal_version=1,
        payload=foreign_payload,
        expected_revision=_git_head(repo),
    )
    foreign_auth = approve(conn, foreign_req_id, person="foreign-approver")
    conn.close()
    foreign_auth_id = foreign_auth.id

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="Fremd-Auth-Test",
                    db_path=str(db_path),
                    repo_path=str(repo),
                    approval_timeout_seconds=3600,
                ),
                id=f"foreign-auth-{uuid.uuid4()}",
                task_queue="pmi-test",
            )
            # Warten bis create_request gelaufen ist, dann Signal senden.
            await _poll_for_request(db_path)
            await handle.signal(InitiationWorkflow.authorized, foreign_auth_id)
            result: WorkflowResult = await handle.result()

    # Commit-Anzahl unverändert
    assert _commit_count(repo) == commits_before, "Kein Commit bei fremder Auth"

    # Keine Execution für den Workflow-Request
    # (Die fremde Auth gehört zu einem anderen Request → begin_execution schlägt fehl)
    assert _execution_count(db_path) == 0, (
        "Keine Execution nach fremder Authorization"
    )

    # Fremde Authorization unbenutzt (used=0)
    assert _auth_used(db_path, foreign_auth_id) == 0, (
        "Fremde Authorization muss unbenutzt bleiben"
    )

    # Keine doppelten dedup_key
    assert _dedup_violations(db_path) == [], "Doppelte dedup_key in AuditEvent"

    # Workflow-Ergebnis: Keine valide Execution (nicht APPLIED)
    assert result.status not in ("APPLIED",), (
        f"Fremde Auth darf nicht zu APPLIED führen: {result.status}"
    )


@pytest.mark.asyncio
async def test_consumed_authorization_rejected(tmp_path, monkeypatch):
    """
    Signal trägt bereits verbrauchte Authorization-ID.

    Ablauf: Erster Workflow läuft bis APPLIED, verbraucht die Authorization.
    Zweiter Workflow erhält dasselbe auth_id als Signal.

    Prüfung:
    - Kein zweiter Commit
    - Kein zweiter Execution-Datensatz
    - Keine doppelten dedup_key
    """
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-temporal")

    db_path = tmp_path / "consumed.db"
    repo = _make_repo(tmp_path, "consumed_repo")
    consumed_auth_id: str | None = None

    # Erster Workflow: normal bis APPLIED
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="Verbraucht-Test-1",
                    db_path=str(db_path),
                    repo_path=str(repo),
                    approval_timeout_seconds=3600,
                ),
                id=f"consumed-first-{uuid.uuid4()}",
                task_queue="pmi-test",
            )
            req_id = await _poll_for_request(db_path)
            assert req_id is not None, "create_request muss einen Request anlegen"

            conn = get_db(str(db_path))
            auth = approve(conn, req_id, person="test-approver")
            conn.close()
            consumed_auth_id = auth.id

            await handle.signal(InitiationWorkflow.authorized, consumed_auth_id)
            first_result: WorkflowResult = await handle.result()

    assert first_result.status == "APPLIED", (
        f"Erster Workflow soll APPLIED sein, war: {first_result.status}"
    )
    assert _auth_used(db_path, consumed_auth_id) == 1, (
        "Authorization muss nach erstem Lauf verbraucht sein"
    )
    commits_after_first = _commit_count(repo)
    executions_after_first = _execution_count(db_path)

    # Zweiter Workflow: Dieselbe (verbrauchte) Authorization als Signal
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            handle2 = await env.client.start_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="Verbraucht-Test-2",
                    db_path=str(db_path),
                    repo_path=str(repo),
                    approval_timeout_seconds=3600,
                ),
                id=f"consumed-second-{uuid.uuid4()}",
                task_queue="pmi-test",
            )
            await _poll_for_request(db_path)
            await handle2.signal(InitiationWorkflow.authorized, consumed_auth_id)
            second_result: WorkflowResult = await handle2.result()

    # Kein zweiter Commit
    assert _commit_count(repo) == commits_after_first, (
        "Kein zweiter Commit nach verbrauchter Authorization"
    )

    # Keine zweite Execution jenseits der ersten
    assert _execution_count(db_path) == executions_after_first, (
        "Keine neue Execution nach verbrauchter Authorization"
    )

    # Keine doppelten dedup_key
    assert _dedup_violations(db_path) == [], "Doppelte dedup_key in AuditEvent"

    # Zweiter Workflow nicht APPLIED
    assert second_result.status not in ("APPLIED",), (
        f"Verbrauchte Auth darf nicht zu APPLIED führen: {second_result.status}"
    )


@pytest.mark.asyncio
async def test_apply_retries_exhausted_reconciles(tmp_path, monkeypatch):
    """
    apply_write scheitert bei jedem Versuch (retryable Fehler). Nach maximum_attempts
    Fehlschlägen ruft der Workflow reconcile auf. Kein Workflow endet mit offener Execution.

    Prüfung:
    - Keine STARTED-Execution am Ende
    - DB-Status FAILED oder ESCALATED (Kern entscheidet, nicht Workflow)
    - Workflow-Ergebnis == DB-Status (K3)
    - zero_effect_proof gesetzt wenn FAILED
    - Keine doppelten dedup_key
    """
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-temporal")

    db_path = tmp_path / "exhausted.db"
    repo = _make_repo(tmp_path, "exhausted_repo")

    # apply_write mit einer Activity ersetzen, die immer einen retriable Fehler wirft.
    from temporalio.exceptions import ApplicationError

    @_ta.defn(name="apply_write")
    async def always_failing_apply_write(args: ApplyWriteInput) -> None:
        raise ApplicationError("Erzwungener Fehlschlag für Test", non_retryable=False)

    # Echte Activities, aber apply_write ausgetauscht.
    test_activities = [
        a for a in ACTIVITIES
        if getattr(getattr(a, "__temporal_activity_definition", None), "name", None)
        != "apply_write"
    ] + [always_failing_apply_write]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=test_activities,
        ):
            handle = await env.client.start_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="Erschöpft-Test",
                    db_path=str(db_path),
                    repo_path=str(repo),
                    approval_timeout_seconds=3600,
                ),
                id=f"exhausted-{uuid.uuid4()}",
                task_queue="pmi-test",
            )

            req_id = await _poll_for_request(db_path)
            assert req_id is not None, "create_request muss einen Request anlegen"

            conn = get_db(str(db_path))
            auth = approve(conn, req_id, person="test-approver")
            conn.close()

            await handle.signal(InitiationWorkflow.authorized, auth.id)
            result: WorkflowResult = await handle.result()

    # Keine offene Execution (K9 aus Plan: Workflow endet nie mit offener Execution)
    assert _open_executions(db_path) == [], (
        "Keine STARTED-Execution nach erschöpften Retries"
    )

    # DB-Status: FAILED oder ESCALATED
    db_status = _execution_status(db_path)
    assert db_status in ("FAILED", "ESCALATED"), (
        f"Erwartet FAILED/ESCALATED nach erschöpften Retries, DB: {db_status}"
    )

    # Workflow-Ergebnis == DB-Status (K3)
    assert result.status == db_status, (
        f"K3 verletzt: Workflow='{result.status}', DB='{db_status}'"
    )

    # FAILED erfordert zero_effect_proof (I3)
    if db_status == "FAILED":
        assert _zero_effect_proof(db_path), "FAILED muss zero_effect_proof enthalten"

    # Keine doppelten dedup_key
    assert _dedup_violations(db_path) == [], "Doppelte dedup_key in AuditEvent"


@pytest.mark.asyncio
async def test_status_matches_control_db(tmp_path, monkeypatch):
    """
    K3: Workflow-Ergebnis muss dem Status in der Kontroll-DB entsprechen.
    Die DB wird nicht dem Workflow angeglichen.

    Drei Läufe: normaler Ablauf, Timeout, ein weiterer normaler Ablauf.
    """
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-temporal")

    # Lauf 1: Normaler Ablauf
    db1 = tmp_path / "k3_normal.db"
    repo1 = _make_repo(tmp_path, "k3_repo_normal")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="K3-Normal",
                    db_path=str(db1),
                    repo_path=str(repo1),
                    approval_timeout_seconds=3600,
                ),
                id=f"k3-normal-{uuid.uuid4()}",
                task_queue="pmi-test",
            )
            req_id = await _poll_for_request(db1)
            assert req_id is not None
            conn = get_db(str(db1))
            auth = approve(conn, req_id, person="k3-approver")
            conn.close()
            await handle.signal(InitiationWorkflow.authorized, auth.id)
            result1: WorkflowResult = await handle.result()

    db1_status = _execution_status(db1)
    assert result1.status == db1_status, (
        f"K3 Lauf 1: Workflow='{result1.status}', DB='{db1_status}'"
    )
    assert _dedup_violations(db1) == [], "Doppelte dedup_key Lauf 1"

    # Lauf 2: Timeout
    db2 = tmp_path / "k3_timeout.db"
    repo2 = _make_repo(tmp_path, "k3_repo_timeout")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pmi-test",
            workflows=[InitiationWorkflow],
            activities=ACTIVITIES,
        ):
            result2: WorkflowResult = await env.client.execute_workflow(
                InitiationWorkflow.run,
                WorkflowInput(
                    task="K3-Timeout",
                    db_path=str(db2),
                    repo_path=str(repo2),
                    approval_timeout_seconds=60,
                ),
                id=f"k3-timeout-{uuid.uuid4()}",
                task_queue="pmi-test",
            )

    assert result2.status == "EXPIRED", (
        f"K3 Lauf 2 (Timeout): erwartet EXPIRED, war: {result2.status}"
    )
    # Kein Execution-Datensatz bei Timeout → DB-Status ist None
    assert _execution_count(db2) == 0, "Keine Execution nach Timeout"
    assert _dedup_violations(db2) == [], "Doppelte dedup_key Lauf 2"


# ── SIGKILL-Tests (Subprozess, kein Temporal Dev-Server) ─────────────────────
#
# Muster analog test_crash.py: Worker-Subprozess endet durch maybe_crash
# (os._exit(1)) am benannten Fault-Point. Danach sauberer Neustart.
# Getestet wird, ob die Temporal-Activity-Wrapper-Schicht dieselbe Idempotenz
# bietet wie der Kern allein.


def _run_worker(scenario: str, db_path: Path, repo_path: Path, fault_point: str) -> int:
    env = _setup_env()
    env["PMI_FAULT_INJECT"] = fault_point
    result = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), scenario, str(db_path), str(repo_path)],
        env=env,
        timeout=60,
    )
    return result.returncode


def _run_worker_clean(scenario: str, db_path: Path, repo_path: Path) -> int:
    env = _setup_env()
    env.pop("PMI_FAULT_INJECT", None)
    result = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), scenario, str(db_path), str(repo_path)],
        env=env,
        timeout=60,
    )
    return result.returncode


def _make_repo_sync(tmp_path: Path, name: str) -> Path:
    """Synchrone Variante für nicht-async Tests."""
    return _make_repo(tmp_path, name)


def test_sigkill_begin_execution_before_persist(tmp_path):
    """
    SIGKILL in begin_execution vor Persist (Fault-Point pre_start_persist).
    Analog C0: nichts verbraucht, Store unverändert, Retry → genau eine Execution.
    """
    for _ in range(SIGKILL_RUNS):
        db_path = tmp_path / f"sk_beg_pre_{uuid.uuid4().hex}.db"
        repo = _make_repo_sync(tmp_path, f"sk_beg_pre_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("temporal_begin_execution", db_path, repo, "pre_start_persist")
        assert rc == -signal.SIGKILL, f"Worker muss per SIGKILL enden, rc={rc}"

        # Kein STARTED-Datensatz
        conn = sqlite3.connect(str(db_path))
        started = conn.execute(
            "SELECT COUNT(*) FROM Execution WHERE status='STARTED'"
        ).fetchone()[0]
        used = conn.execute("SELECT used FROM Authorization LIMIT 1").fetchone()
        conn.close()

        assert started == 0, "Kein STARTED nach Kill vor pre_start_persist"
        if used is not None:
            assert used[0] == 0, "Authorization muss unverbraucht sein"
        assert _commit_count(repo) == commits_before

        # Retry → genau eine Execution
        rc_clean = _run_worker_clean("temporal_begin_execution", db_path, repo)
        assert rc_clean == 0, "Retry muss erfolgreich sein"

        conn = sqlite3.connect(str(db_path))
        execution_count = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
        conn.close()
        assert execution_count == 1, "Genau eine Execution nach Retry"
        assert _dedup_violations(db_path) == [], "Doppelte dedup_key"


def test_sigkill_begin_execution_after_persist(tmp_path):
    """
    SIGKILL in begin_execution nach Persist.
    Analog C1b: Retry erkennt eigenen Verbrauch, genau eine Execution.
    """
    for _ in range(SIGKILL_RUNS):
        db_path = tmp_path / f"sk_beg_post_{uuid.uuid4().hex}.db"
        repo = _make_repo_sync(tmp_path, f"sk_beg_post_{uuid.uuid4().hex}")

        rc = _run_worker("temporal_begin_execution", db_path, repo, "post_start_persist")
        assert rc == -signal.SIGKILL, f"Worker muss per SIGKILL enden, rc={rc}"

        # Retry
        rc_clean = _run_worker_clean("temporal_begin_execution", db_path, repo)
        assert rc_clean == 0

        conn = sqlite3.connect(str(db_path))
        execution_count = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
        conn.close()
        assert execution_count == 1, "Genau eine Execution nach post-persist Kill + Retry"
        assert _dedup_violations(db_path) == [], "Doppelte dedup_key"


def test_sigkill_apply_write_before_commit(tmp_path):
    """
    SIGKILL in apply_write vor Commit (Fault-Point post_rename_pre_commit).
    Analog C4a: Bereinigung, genau ein Commit nach Retry, git status sauber.
    """
    for _ in range(SIGKILL_RUNS):
        db_path = tmp_path / f"sk_apply_pre_{uuid.uuid4().hex}.db"
        repo = _make_repo_sync(tmp_path, f"sk_apply_pre_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("temporal_apply_write", db_path, repo, "post_rename_pre_commit")
        assert rc == -signal.SIGKILL, f"Worker muss per SIGKILL enden, rc={rc}"

        assert _commit_count(repo) == commits_before

        rc_clean = _run_worker_clean("temporal_apply_write", db_path, repo)
        assert rc_clean == 0
        assert _commit_count(repo) == commits_before + 1

        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, check=True,
        )
        assert result.stdout.decode().strip() == "", "git status muss sauber sein"
        assert _dedup_violations(db_path) == [], "Doppelte dedup_key"


def test_sigkill_apply_write_after_commit(tmp_path):
    """
    SIGKILL in apply_write nach Commit, vor DB-Ack (Fault-Point post_commit).
    Analog C3: ALREADY_APPLIED, kein zweiter Commit, Status APPLIED.
    """
    for _ in range(SIGKILL_RUNS):
        db_path = tmp_path / f"sk_apply_post_{uuid.uuid4().hex}.db"
        repo = _make_repo_sync(tmp_path, f"sk_apply_post_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("temporal_apply_write", db_path, repo, "post_commit")
        assert rc == -signal.SIGKILL, f"Worker muss per SIGKILL enden, rc={rc}"

        assert _commit_count(repo) == commits_before + 1

        rc_clean = _run_worker_clean("temporal_apply_write", db_path, repo)
        assert rc_clean == 0
        assert _commit_count(repo) == commits_before + 1, "Kein zweiter Commit"

        conn = sqlite3.connect(str(db_path))
        status = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert status is not None and status[0] == "APPLIED"
        assert _dedup_violations(db_path) == [], "Doppelte dedup_key"


def test_sigkill_reconcile_after_divergent(tmp_path):
    """
    SIGKILL nach reconcile-Übergang (Fault-Point after_reconcile_transition).
    Retry: idempotent – kein doppelter Übergang, Status ESCALATED oder APPLIED.
    """
    for _ in range(SIGKILL_RUNS):
        db_path = tmp_path / f"sk_rec_{uuid.uuid4().hex}.db"
        repo = _make_repo_sync(tmp_path, f"sk_rec_{uuid.uuid4().hex}")

        # APPLIED-Zustand herstellen
        rc = _run_worker_clean("temporal_apply_write", db_path, repo)
        assert rc == 0

        # Zieldatei manipulieren → DIVERGENT beim Reconcile
        target = repo / "temporal_output.txt"
        if target.exists():
            target.write_text("tampered by sigkill-test")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "tampered-for-sigkill-test"],
                cwd=repo, check=True, capture_output=True,
            )

        rc = _run_worker("temporal_reconcile", db_path, repo, "after_reconcile_transition")
        assert rc == -signal.SIGKILL, f"Worker muss per SIGKILL enden, rc={rc}"

        # Retry
        rc_clean = _run_worker_clean("temporal_reconcile", db_path, repo)
        assert rc_clean == 0

        db_status = _execution_status(db_path)
        assert db_status == "ESCALATED", (
            f"Manipulierte Datei muss ESCALATED ergeben, war: {db_status}"
        )
        assert _dedup_violations(db_path) == [], "Doppelte dedup_key nach Reconcile-Kill"

"""
Subprocess-Worker für Temporal-SIGKILL-Tests.

Aufruf: python tests/worker_temporal.py <scenario> <db_path> <repo_path>
PMI_FAULT_INJECT=<point> wird als Umgebungsvariable erwartet.

Szenarien:
  temporal_begin_execution  – Stellt DB-Zustand her, ruft begin_execution-Activity auf
  temporal_apply_write      – Stellt DB-Zustand her, ruft apply_write-Activity auf
  temporal_reconcile        – Ruft reconcile-Activity auf (setzt APPLIED voraus)

Die Activity-Funktionen aus pmi.temporal.activities werden direkt aufgerufen
(kein Temporal-Server). Fault-Injection via maybe_crash(point) in den Kernel-
Funktionen oder via PMI_FAULT_INJECT in den Activity-Wrappern selbst.
"""
import subprocess
import sys
import uuid
from pathlib import Path


def _git_head(repo_path: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path,
    ).decode().strip()


def _setup_db(db_path: str):
    from pmi.db import get_db, init_schema
    conn = get_db(db_path)
    init_schema(conn)
    return conn


def _setup_request(conn, payload, expected_revision=""):
    from pmi.db import insert_proposal, insert_request
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    request_id = insert_request(
        conn,
        proposal_id=proposal_id,
        proposal_version=1,
        payload=payload,
        expected_revision=expected_revision,
    )
    return proposal_id, request_id


def _minimal_payload() -> dict:
    return {"action": "touch_file", "path": "temporal_output.txt", "scope": "local"}


def _workflow_id_from(seed: str) -> str:
    """Deterministisch aus seed (z. B. request_hash) – bleibt über Crashes hinweg gleich."""
    return f"pmi-exec-{seed[:24]}"


def scenario_temporal_begin_execution(db_path: str, repo_path: str) -> None:
    """
    Stellt DB-Zustand (Request + Authorization) her und ruft die
    begin_execution-Activity direkt auf.

    Bei Crash-Recovery (STARTED-Zeile vorhanden): liest gespeicherte Eingaben
    aus der DB und ruft die Activity erneut auf – testet Idempotenz des Wrappers.
    workflow_id wird deterministisch aus request_hash abgeleitet: bleibt über
    Crashes hinweg gleich, damit der dedup_key konstant bleibt.
    """
    from pmi.approval import approve
    from pmi.temporal.activities import BeginExecutionInput, activity_begin_execution

    conn = _setup_db(db_path)
    payload = _minimal_payload()

    # Recovery: vorhandene STARTED-Execution → selbe Eingaben wiederverwenden
    existing = conn.execute(
        "SELECT e.idempotency_key, r.id AS request_id "
        "FROM Execution e "
        "JOIN ExecutionRequest r ON r.request_hash = e.idempotency_key "
        "WHERE e.status='STARTED' "
        "ORDER BY e.started_at DESC LIMIT 1"
    ).fetchone()

    if existing:
        idem_key = existing["idempotency_key"]
        request_id = existing["request_id"]
        auth_row = conn.execute(
            "SELECT id FROM Authorization WHERE request_id=? "
            "ORDER BY granted_at DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        auth_id = auth_row["id"]
    else:
        head = _git_head(repo_path)
        _, request_id = _setup_request(conn, payload, expected_revision=head)
        auth = approve(conn, request_id, person="worker-temporal-approver")
        auth_id = auth.id
        req_row = conn.execute(
            "SELECT request_hash FROM ExecutionRequest WHERE id=?", (request_id,)
        ).fetchone()
        idem_key = req_row["request_hash"]

    wf_id = _workflow_id_from(idem_key)
    conn.close()

    activity_begin_execution(BeginExecutionInput(
        db_path=db_path,
        repo_path=repo_path,
        request_id=request_id,
        auth_id=auth_id,
        idempotency_key=idem_key,
        step=6,
        workflow_id=wf_id,
    ))


def scenario_temporal_apply_write(db_path: str, repo_path: str) -> None:
    """
    Stellt DB-Zustand (STARTED-Execution) her und ruft die
    apply_write-Activity direkt auf.

    Idempotenz: Gibt es bereits eine STARTED-Execution, wird deren
    Idempotenzschlüssel weiterverwendet.
    """
    from pmi.approval import approve
    from pmi.state_machine import begin_execution
    from pmi.temporal.activities import ApplyWriteInput, activity_apply_write

    conn = _setup_db(db_path)
    payload = _minimal_payload()

    existing = conn.execute(
        "SELECT id, idempotency_key FROM Execution WHERE status='STARTED' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if existing:
        execution_id = existing["id"]
        idem_key = existing["idempotency_key"]
    else:
        head = _git_head(repo_path)
        _, request_id = _setup_request(conn, payload, expected_revision=head)
        auth = approve(conn, request_id, person="worker-temporal-approver")

        # Idempotenzschlüssel = request_hash aus dem Plan (Schritt 7)
        req_row = conn.execute(
            "SELECT request_hash FROM ExecutionRequest WHERE id=?", (request_id,)
        ).fetchone()
        idem_key = req_row["request_hash"]

        execution_id = begin_execution(
            conn,
            request_id=request_id,
            auth_id=auth.id,
            idempotency_key=idem_key,
            repo_path=repo_path,
        )

    exec_row = conn.execute(
        "SELECT expected_revision FROM Execution WHERE id=?", (execution_id,)
    ).fetchone()
    expected_rev = exec_row["expected_revision"] or ""
    conn.close()

    activity_apply_write(ApplyWriteInput(
        db_path=db_path,
        repo_path=repo_path,
        payload=payload,
        expected_revision=expected_rev,
        idempotency_key=idem_key,
        execution_id=execution_id,
        step=7,
        workflow_id=_workflow_id_from(idem_key),
    ))


def scenario_temporal_reconcile(db_path: str, repo_path: str) -> None:
    """
    Ruft die reconcile-Activity direkt auf.
    Setzt voraus, dass mindestens eine Execution in DB existiert.

    workflow_id wird aus dem idempotency_key der letzten Execution abgeleitet –
    deterministisch über Crashes hinweg.
    """
    from pmi.temporal.activities import ReconcileInput, activity_reconcile
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    exec_row = conn.execute(
        "SELECT idempotency_key FROM Execution ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    idem_key = exec_row["idempotency_key"] if exec_row else str(uuid.uuid4())

    activity_reconcile(ReconcileInput(
        db_path=db_path,
        repo_path=repo_path,
        step=9,
        workflow_id=_workflow_id_from(idem_key),
    ))


SCENARIOS = {
    "temporal_begin_execution": scenario_temporal_begin_execution,
    "temporal_apply_write": scenario_temporal_apply_write,
    "temporal_reconcile": scenario_temporal_reconcile,
}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Usage: worker_temporal.py <scenario> <db_path> <repo_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    _, scenario, db_path, repo_path = sys.argv
    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}", file=sys.stderr)
        sys.exit(2)
    SCENARIOS[scenario](db_path, repo_path)

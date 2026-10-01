"""
Subprocess-Worker für Crashtests.

Aufruf: python tests/worker_crash.py <scenario> <db_path> <repo_path>
PMI_FAULT_INJECT=<point> muss als Umgebungsvariable gesetzt sein.

Szenarien:
  begin_execution  – Führt begin_execution() aus
  adapter_apply    – Führt adapter.apply() aus (setzt voraus: STARTED in DB)
  authorization    – Führt approve() aus
  reconcile        – Ruft adapter.reconcile_applied_executions() auf

Gibt Return-Code 0 bei Erfolg (ohne Crash), oder wird durch maybe_crash() mit os._exit(1) beendet.
"""
import subprocess
import sys
import uuid
from pathlib import Path


def _git_head(repo_path: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path,
    ).decode().strip()


def _setup_request(conn, payload, expected_revision=""):
    from pmi.db import insert_proposal, insert_request
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    request_id = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1, payload=payload,
        expected_revision=expected_revision,
    )
    return proposal_id, request_id


def scenario_begin_execution(db_path: str, repo_path: str) -> None:
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    head = _git_head(repo_path)
    _, request_id = _setup_request(conn, payload, expected_revision=head)
    auth = approve(conn, request_id, person="alice")
    idem_key = str(uuid.uuid4())
    begin_execution(conn, request_id=request_id, auth_id=auth.id, idempotency_key=idem_key,
                    repo_path=repo_path)


def scenario_adapter_apply(db_path: str, repo_path: str) -> None:
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution
    from pmi.git_adapter import GitProjectStoreAdapter

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}

    # Reconciliation: vorhandene STARTED-Execution wiederverwenden (nach Crash)
    existing = conn.execute(
        "SELECT id, idempotency_key FROM Execution WHERE status='STARTED' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if existing:
        execution_id = existing["id"]
        idem_key = existing["idempotency_key"]
    else:
        # HEAD einmalig bei Request-Erstellung lesen (nicht nach begin_execution)
        head = _git_head(repo_path)
        _, request_id = _setup_request(conn, payload, expected_revision=head)
        auth = approve(conn, request_id, person="alice")
        idem_key = str(uuid.uuid4())
        execution_id = begin_execution(
            conn, request_id=request_id, auth_id=auth.id, idempotency_key=idem_key,
            repo_path=repo_path,
        )

    # expected_revision aus Execution lesen (stammt aus Request-Erstellung)
    exec_row = conn.execute(
        "SELECT expected_revision FROM Execution WHERE id=?", (execution_id,)
    ).fetchone()
    expected_rev = exec_row["expected_revision"] or ""

    adapter = GitProjectStoreAdapter(repo_path=Path(repo_path), db=conn)
    adapter.apply(
        payload=payload,
        expected_revision=expected_rev,
        idempotency_key=idem_key,
        execution_id=execution_id,
    )


def scenario_authorization(db_path: str, repo_path: str) -> None:
    from pmi.db import get_db, init_schema
    from pmi.approval import approve

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    _, request_id = _setup_request(conn, payload)
    approve(conn, request_id, person="alice")


def scenario_reconcile(db_path: str, repo_path: str) -> None:
    from pmi.db import get_db, init_schema
    from pmi.git_adapter import GitProjectStoreAdapter

    conn = get_db(db_path)
    init_schema(conn)
    adapter = GitProjectStoreAdapter(repo_path=Path(repo_path), db=conn)
    adapter.reconcile_applied_executions()


def scenario_c6_fail_attempt(db_path: str, repo_path: str) -> None:
    """C6-Gegenprobe: versucht FAILED mit zero_effect_proof zu setzen.
    Schlägt fehl wenn HEAD sich geändert hat (ohne Break); ohne HEAD-Prüfung (C6-Break) → FAILED gesetzt."""
    from pmi.db import get_db, init_schema
    from pmi.state_machine import ExecutionStatus, transition

    conn = get_db(db_path)
    init_schema(conn)
    existing = conn.execute(
        "SELECT id FROM Execution WHERE status='STARTED' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if existing is None:
        raise RuntimeError("Kein STARTED-Datensatz gefunden")
    transition(conn, execution_id=existing["id"], to_state=ExecutionStatus.FAILED,
               zero_effect_proof="kein-Schreibvorgang-Gegenprobe")


SCENARIOS = {
    "begin_execution": scenario_begin_execution,
    "adapter_apply": scenario_adapter_apply,
    "authorization": scenario_authorization,
    "reconcile": scenario_reconcile,
    "c6_fail_attempt": scenario_c6_fail_attempt,
}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: worker_crash.py <scenario> <db_path> <repo_path>", file=sys.stderr)
        sys.exit(2)
    _, scenario, db_path, repo_path = sys.argv
    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}", file=sys.stderr)
        sys.exit(2)
    SCENARIOS[scenario](db_path, repo_path)

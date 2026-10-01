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


def scenario_cp_c0(db_path: str, repo_path: str) -> None:
    """C0-Gegenprobe: Authorization.used=1 vor Transaktion schreiben → überlebt Crash."""
    import os as _os
    from pmi.db import get_db, init_schema
    from pmi.approval import approve

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    head = _git_head(repo_path)
    _, request_id = _setup_request(conn, payload, expected_revision=head)
    auth = approve(conn, request_id, person="alice")
    # BREAK: mark used=1 outside the begin_execution transaction
    conn.execute("UPDATE Authorization SET used=1 WHERE id=?", (auth.id,))
    conn.commit()
    _os._exit(1)


def scenario_cp_c1(db_path: str, repo_path: str) -> None:
    """C1-Gegenprobe: _cleanup_orphaned_tmp_files deaktiviert → orphaned .pmi_tmp_* bleibt."""
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution
    from pmi.git_adapter import GitProjectStoreAdapter

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}

    existing = conn.execute(
        "SELECT id, idempotency_key FROM Execution WHERE status='STARTED' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if existing:
        execution_id = existing["id"]
        idem_key = existing["idempotency_key"]
        expected_rev = conn.execute(
            "SELECT expected_revision FROM Execution WHERE id=?", (execution_id,)
        ).fetchone()["expected_revision"] or ""
    else:
        head = _git_head(repo_path)
        _, request_id = _setup_request(conn, payload, expected_revision=head)
        auth = approve(conn, request_id, person="alice")
        idem_key = str(uuid.uuid4())
        execution_id = begin_execution(conn, request_id=request_id, auth_id=auth.id,
                                       idempotency_key=idem_key, repo_path=repo_path)
        expected_rev = head

    # BREAK: disable orphaned temp-file cleanup
    GitProjectStoreAdapter._cleanup_orphaned_tmp_files = lambda self: None

    adapter = GitProjectStoreAdapter(repo_path=Path(repo_path), db=conn)
    adapter.apply(payload=payload, expected_revision=expected_rev,
                  idempotency_key=idem_key, execution_id=execution_id)


def scenario_cp_c3(db_path: str, repo_path: str) -> None:
    """C3-Gegenprobe: Authorization außerhalb Transaktion / leere Signatur → halbfertiger Datensatz."""
    import os as _os
    from pmi.db import get_db, init_schema

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    _, request_id = _setup_request(conn, payload)
    auth_id = str(uuid.uuid4())
    # BREAK: write partial record (no signature) outside transaction
    conn.execute(
        "INSERT INTO Authorization (id, request_id, decided_by, expires_at, used, signature) "
        "VALUES (?, ?, ?, datetime('now', '+86400 seconds'), 0, '')",
        (auth_id, request_id, "human:alice"),
    )
    conn.commit()
    _os._exit(1)


def scenario_cp_c4a(db_path: str, repo_path: str) -> None:
    """C4a-Gegenprobe: _cleanup_leftover + _atomic_rename deaktiviert → tampered content committed."""
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution
    from pmi.git_adapter import GitProjectStoreAdapter

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}

    existing = conn.execute(
        "SELECT id, idempotency_key FROM Execution WHERE status='STARTED' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if existing:
        execution_id = existing["id"]
        idem_key = existing["idempotency_key"]
        expected_rev = conn.execute(
            "SELECT expected_revision FROM Execution WHERE id=?", (execution_id,)
        ).fetchone()["expected_revision"] or ""
    else:
        head = _git_head(repo_path)
        _, request_id = _setup_request(conn, payload, expected_revision=head)
        auth = approve(conn, request_id, person="alice")
        idem_key = str(uuid.uuid4())
        execution_id = begin_execution(conn, request_id=request_id, auth_id=auth.id,
                                       idempotency_key=idem_key, repo_path=repo_path)
        expected_rev = head

    # Write tampered content to target file
    target = Path(repo_path) / str(payload.get("path", "output.txt"))
    target.write_text("TAMPERED")

    # BREAK: disable cleanup and rename so tampered content gets committed
    GitProjectStoreAdapter._cleanup_leftover = lambda self, p, k: None
    GitProjectStoreAdapter._atomic_rename = lambda self, tmp, tgt: tmp.unlink(missing_ok=True)

    adapter = GitProjectStoreAdapter(repo_path=Path(repo_path), db=conn)
    adapter.apply(payload=payload, expected_revision=expected_rev,
                  idempotency_key=idem_key, execution_id=execution_id)


def scenario_cp_c6(db_path: str, repo_path: str) -> None:
    """C6-Gegenprobe: HEAD-Prüfung deaktiviert → FAILED trotz geändertem HEAD."""
    from pmi.db import get_db, init_schema
    from pmi.state_machine import ExecutionStatus, transition

    conn = get_db(db_path)
    init_schema(conn)
    existing = conn.execute(
        "SELECT id FROM Execution WHERE status='STARTED' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if existing is None:
        raise RuntimeError("Kein STARTED-Datensatz gefunden")
    # BREAK: clear repo_path so HEAD check is skipped in transition(FAILED)
    conn.execute("UPDATE Execution SET repo_path='' WHERE id=?", (existing["id"],))
    conn.commit()
    transition(conn, execution_id=existing["id"], to_state=ExecutionStatus.FAILED,
               zero_effect_proof="kein-Schreibvorgang-Gegenprobe-c6")


SCENARIOS = {
    "begin_execution": scenario_begin_execution,
    "adapter_apply": scenario_adapter_apply,
    "authorization": scenario_authorization,
    "reconcile": scenario_reconcile,
    "cp_c0": scenario_cp_c0,
    "cp_c1": scenario_cp_c1,
    "cp_c3": scenario_cp_c3,
    "cp_c4a": scenario_cp_c4a,
    "cp_c6": scenario_cp_c6,
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

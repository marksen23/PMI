"""
Subprocess-Worker für Crashtests.

Aufruf: python tests/worker_crash.py <scenario> <db_path> <repo_path>
PMI_FAULT_INJECT=<point> muss als Umgebungsvariable gesetzt sein.

Szenarien:
  begin_execution  – Führt begin_execution() aus
  adapter_apply    – Führt adapter.apply() aus (setzt voraus: STARTED in DB)
  authorization    – Führt approve() aus

Gibt Return-Code 0 bei Erfolg (ohne Crash), oder wird durch maybe_crash() mit os._exit(1) beendet.
"""
import sys
import uuid
from pathlib import Path


def _setup_request(conn, payload):
    from pmi.db import insert_proposal, insert_request
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    request_id = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1, payload=payload
    )
    return proposal_id, request_id


def scenario_begin_execution(db_path: str, repo_path: str) -> None:
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    _, request_id = _setup_request(conn, payload)
    auth = approve(conn, request_id, person="alice")
    idem_key = str(uuid.uuid4())
    begin_execution(conn, request_id=request_id, auth_id=auth.id, idempotency_key=idem_key)


def scenario_adapter_apply(db_path: str, repo_path: str) -> None:
    import subprocess
    from pmi.db import get_db, init_schema
    from pmi.approval import approve
    from pmi.state_machine import begin_execution
    from pmi.git_adapter import GitProjectStoreAdapter

    conn = get_db(db_path)
    init_schema(conn)
    payload = {"action": "touch_file", "path": "worker_output.txt", "scope": "local"}
    _, request_id = _setup_request(conn, payload)
    auth = approve(conn, request_id, person="alice")
    idem_key = str(uuid.uuid4())
    execution_id = begin_execution(
        conn, request_id=request_id, auth_id=auth.id, idempotency_key=idem_key
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path,
    ).decode().strip()
    adapter = GitProjectStoreAdapter(repo_path=Path(repo_path), db=conn)
    adapter.apply(
        payload=payload,
        expected_revision=head,
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


SCENARIOS = {
    "begin_execution": scenario_begin_execution,
    "adapter_apply": scenario_adapter_apply,
    "authorization": scenario_authorization,
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

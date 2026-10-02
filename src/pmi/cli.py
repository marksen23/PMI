"""CLI: pmi approve / run / status"""
import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: pmi <approve|run|status> ...", file=sys.stderr)
        sys.exit(1)

    cmd, rest = args[0], args[1:]

    if cmd == "approve":
        _cmd_approve(rest)
    elif cmd == "run":
        _cmd_run(rest)
    elif cmd == "status":
        _cmd_status(rest)
    else:
        print(f"Unbekannter Befehl: {cmd}", file=sys.stderr)
        sys.exit(1)


def _get_db():
    import os
    from pmi.db import get_db, init_schema
    db_path = os.environ.get("PMI_DB_PATH", "pmi_control.db")
    conn = get_db(db_path)
    init_schema(conn)
    return conn


def _cmd_approve(args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: pmi approve <request_id> <person>", file=sys.stderr)
        sys.exit(1)
    request_id, person = args
    from pmi.approval import approve
    conn = _get_db()
    auth = approve(conn, request_id, person=person)
    print(f"Authorization erteilt: {auth.id}")


def _cmd_run(args: list[str]) -> None:
    import asyncio
    import os

    from temporalio.client import Client

    from pmi.temporal.workflow import InitiationWorkflow

    if len(args) != 1:
        print("Usage: pmi run <request_id>", file=sys.stderr)
        sys.exit(1)

    request_id = args[0]
    temporal_host = os.environ.get("PMI_TEMPORAL_HOST", "localhost:7233")

    conn = _get_db()

    req_row = conn.execute(
        "SELECT status, workflow_id FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()
    if req_row is None:
        print(f"run: unbekannter request_id: {request_id}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    if req_row["status"] == "EXPIRED":
        print(f"run: Request {request_id} ist abgelaufen (EXPIRED)", file=sys.stderr)
        conn.close()
        sys.exit(1)

    workflow_id = req_row["workflow_id"]
    if not workflow_id:
        print(f"run: kein workflow_id für request_id: {request_id}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    auth_row = conn.execute(
        "SELECT id FROM Authorization "
        "WHERE request_id=? AND used=0 AND expires_at > datetime('now') "
        "ORDER BY granted_at DESC LIMIT 1",
        (request_id,),
    ).fetchone()
    if auth_row is None:
        print(f"run: keine gültige Authorization für request_id: {request_id}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    auth_id = auth_row["id"]
    conn.close()

    async def _signal() -> None:
        client = await Client.connect(temporal_host)
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(InitiationWorkflow.authorized, auth_id)

    try:
        asyncio.run(_signal())
    except Exception as e:
        print(f"run: Signal fehlgeschlagen: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


def _cmd_status(args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: pmi status <request_id>", file=sys.stderr)
        sys.exit(1)
    request_id = args[0]
    conn = _get_db()
    row = conn.execute(
        "SELECT e.status, e.started_at, e.finished_at "
        "FROM Execution e "
        "JOIN ExecutionRequest er ON er.id = e.request_id "
        "WHERE er.id=? "
        "ORDER BY e.started_at DESC LIMIT 1",
        (request_id,),
    ).fetchone()
    if row is None:
        print(f"Kein Execution-Datensatz für {request_id}")
    else:
        print(f"Status: {row['status']}, Gestartet: {row['started_at']}, Beendet: {row['finished_at']}")

"""CLI: pmi init / start / show / approve / run / status / eval / answers / worker"""
from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage: pmi <init|start|show|approve|run|status|eval|answers|worker> ...",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd, rest = args[0], args[1:]

    if cmd == "init":
        _cmd_init(rest)
    elif cmd == "start":
        _cmd_start(rest)
    elif cmd == "show":
        _cmd_show(rest)
    elif cmd == "approve":
        _cmd_approve(rest)
    elif cmd == "run":
        _cmd_run(rest)
    elif cmd == "status":
        _cmd_status(rest)
    elif cmd == "eval":
        _cmd_eval(rest)
    elif cmd == "answers":
        _cmd_answers(rest)
    elif cmd == "worker":
        _cmd_worker(rest)
    else:
        print(f"Unbekannter Befehl: {cmd}", file=sys.stderr)
        sys.exit(1)


def _env(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default)


def _db_path() -> str:
    return _env("PMI_DB_PATH", "pmi_control.db")


def _get_db():
    from pmi.db import get_db, init_schema

    conn = get_db(_db_path())
    init_schema(conn)
    return conn


def _cmd_init(args: list[str]) -> None:
    if args:
        print("Usage: pmi init", file=sys.stderr)
        sys.exit(1)
    path = _db_path()
    conn = _get_db()
    conn.close()
    print(f"Schema initialisiert: {path}")


def _cmd_start(args: list[str]) -> None:
    import asyncio
    import time
    import uuid

    from temporalio.client import Client

    from pmi.temporal.workflow import InitiationWorkflow, WorkflowInput

    if not args:
        print('Usage: pmi start "<task>"', file=sys.stderr)
        sys.exit(1)

    task = " ".join(args)
    db_path = _db_path()
    repo_path = _env("PMI_REPO_PATH", ".")
    host = _env("PMI_TEMPORAL_HOST", "localhost:7233")
    queue = _env("PMI_TASK_QUEUE", "pmi-initiation")
    approval_timeout = int(_env("PMI_APPROVAL_TIMEOUT_SECONDS", "3600"))
    wait_seconds = float(_env("PMI_START_WAIT_SECONDS", "120"))

    # Schema sicherstellen, bevor der Worker Activities schreibt
    conn = _get_db()
    conn.close()

    workflow_id = f"pmi-{uuid.uuid4()}"

    async def _run() -> tuple[str, str, str]:
        client = await Client.connect(host)
        await client.start_workflow(
            InitiationWorkflow.run,
            WorkflowInput(
                task=task,
                db_path=db_path,
                repo_path=repo_path,
                approval_timeout_seconds=approval_timeout,
            ),
            id=workflow_id,
            task_queue=queue,
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            c = _get_db()
            row = c.execute(
                "SELECT id, proposal_id FROM ExecutionRequest WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
            c.close()
            if row is not None:
                return workflow_id, row["id"], row["proposal_id"]
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"start: kein ExecutionRequest für workflow_id={workflow_id} "
            f"innerhalb von {wait_seconds}s"
        )

    try:
        wf_id, request_id, proposal_id = asyncio.run(_run())
    except Exception as exc:
        print(f"start: fehlgeschlagen: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"workflow_id={wf_id}")
    print(f"request_id={request_id}")
    print(f"proposal_id={proposal_id}")


def _cmd_show(args: list[str]) -> None:
    import json

    if len(args) != 1:
        print("Usage: pmi show <request_id>", file=sys.stderr)
        sys.exit(1)

    request_id = args[0]
    conn = _get_db()
    row = conn.execute(
        "SELECT p.payload_json FROM ExecutionRequest er "
        "JOIN Proposal p ON p.id = er.proposal_id "
        "WHERE er.id=?",
        (request_id,),
    ).fetchone()
    conn.close()
    if row is None:
        print(f"show: unbekannter request_id: {request_id}", file=sys.stderr)
        sys.exit(1)
    # Canonical JSON for stdout (ProposalOutput payload)
    payload = json.loads(row["payload_json"])
    print(json.dumps(payload, ensure_ascii=False))


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

    from temporalio.client import Client

    from pmi.temporal.workflow import InitiationWorkflow

    if len(args) != 1:
        print("Usage: pmi run <request_id>", file=sys.stderr)
        sys.exit(1)

    request_id = args[0]
    temporal_host = _env("PMI_TEMPORAL_HOST", "localhost:7233")

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
    req = conn.execute(
        "SELECT id, status, workflow_id, proposal_id FROM ExecutionRequest WHERE id=?",
        (request_id,),
    ).fetchone()
    if req is None:
        print(f"status: unbekannter request_id: {request_id}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    print(f"request_id={req['id']}")
    print(f"status={req['status']}")
    print(f"workflow_id={req['workflow_id'] or ''}")
    print(f"proposal_id={req['proposal_id']}")

    exe = conn.execute(
        "SELECT status, started_at, finished_at FROM Execution "
        "WHERE request_id=? ORDER BY started_at DESC LIMIT 1",
        (request_id,),
    ).fetchone()
    conn.close()
    if exe is not None:
        print(
            f"execution_status={exe['status']}, "
            f"Gestartet: {exe['started_at']}, Beendet: {exe['finished_at']}"
        )


def _cmd_eval(args: list[str]) -> None:
    import json
    from dataclasses import asdict, is_dataclass

    if len(args) != 2:
        print(
            "Usage: pmi eval <justitia|ihp|nd_bed> <request_id>",
            file=sys.stderr,
        )
        sys.exit(1)

    which, request_id = args
    from pmi.agent_schema import ProposalOutput

    conn = _get_db()
    row = conn.execute(
        "SELECT p.payload_json FROM ExecutionRequest er "
        "JOIN Proposal p ON p.id = er.proposal_id "
        "WHERE er.id=?",
        (request_id,),
    ).fetchone()
    conn.close()
    if row is None:
        print(f"eval: unbekannter request_id: {request_id}", file=sys.stderr)
        sys.exit(1)

    output = ProposalOutput.model_validate(json.loads(row["payload_json"]))

    if which == "justitia":
        from pmi.eval_justitia import evaluate
    elif which == "ihp":
        from pmi.eval_ihp import evaluate
    elif which == "nd_bed":
        from pmi.eval_nd_bed_management import evaluate
    else:
        print(f"eval: unbekannter Prüfer: {which}", file=sys.stderr)
        sys.exit(1)

    result = evaluate(output)
    if is_dataclass(result) and not isinstance(result, type):
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    else:
        print(json.dumps(result, ensure_ascii=False, default=str))


def _cmd_answers(args: list[str]) -> None:
    """Stub Auftrag 10 HP2 – noch keine K4-Persistenz."""
    print(
        "Usage: pmi answers <proposal_id> <person> -- <answer> [<answer> ...]",
        file=sys.stderr,
    )
    # Stub: keine v2, kein Signal – Tests sollen an Assertions scheitern.
    if len(args) < 2:
        sys.exit(1)
    print(f"answers: stub (proposal_id={args[0]})")


def _cmd_worker(args: list[str]) -> None:
    import asyncio

    from pmi.temporal.worker import main as worker_main

    if args:
        print("Usage: pmi worker", file=sys.stderr)
        sys.exit(1)
    asyncio.run(worker_main())

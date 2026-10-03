"""Activity-Wrapper um den PMI-Kern für den InitiationWorkflow."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import temporalio.activity as _activity

from pmi.agent import FakeLLMProvider, LLMProvider, run_agent
from pmi.db import (
    append_audit,
    get_db,
    init_schema,
    insert_request,
)
from pmi.git_adapter import GitProjectStoreAdapter
from pmi.state_machine import (
    AuthorizationError,
    ExecutionStatus,
    TransitionError,
    begin_execution as _begin_execution,
    fail_revision_conflict,
    transition,
)


def _dedup(workflow_id: str, step: int, event_type: str) -> str:
    return f"{workflow_id}:{step}:{event_type}"


def get_llm_provider() -> LLMProvider:
    """E1: FakeLLM wenn PMI_FAKE_LLM_JSON gesetzt, sonst ClaudeLLMProvider."""
    fake_json = os.environ.get("PMI_FAKE_LLM_JSON", "")
    if fake_json:
        return FakeLLMProvider(fake_json)
    from pmi.claude_provider import ClaudeLLMProvider

    return ClaudeLLMProvider()


# ── Input / Result-Dataclasses ────────────────────────────────────────────────

@dataclass
class DraftProposalInput:
    task: str
    db_path: str
    step: int
    workflow_id: str


@dataclass
class DraftProposalResult:
    proposal_id: str
    payload: dict


@dataclass
class CreateRequestInput:
    db_path: str
    repo_path: str
    proposal_id: str
    step: int
    workflow_id: str


@dataclass
class CreateRequestResult:
    request_id: str
    request_hash: str
    expected_revision: str


@dataclass
class MarkRequestExpiredInput:
    db_path: str
    request_id: str
    step: int
    workflow_id: str


@dataclass
class BeginExecutionInput:
    db_path: str
    repo_path: str
    request_id: str
    auth_id: str
    idempotency_key: str  # = request_hash aus ExecutionRequest
    step: int
    workflow_id: str


@dataclass
class ApplyWriteInput:
    db_path: str
    repo_path: str
    payload: dict
    expected_revision: str
    idempotency_key: str
    execution_id: str
    step: int
    workflow_id: str


@dataclass
class VerifyWriteInput:
    db_path: str
    repo_path: str
    execution_id: str
    step: int
    workflow_id: str


@dataclass
class ReconcileInput:
    db_path: str
    repo_path: str
    step: int
    workflow_id: str


@dataclass
class ReadExecutionStatusInput:
    db_path: str
    execution_id: str
    step: int
    workflow_id: str


# ── Activity-Funktionen (async, laufen im Event-Loop; Blocking-I/O ist im
#    Test-Kontext akzeptabel; direkte Aufrufe aus worker_temporal.py via
#    asyncio.run()) ──────────────────────────────────────────────────────────

@_activity.defn(name="draft_proposal")
async def activity_draft_proposal(args: DraftProposalInput) -> DraftProposalResult:
    conn = get_db(args.db_path)
    init_schema(conn)
    dedup_key = _dedup(args.workflow_id, args.step, "DRAFT_PROPOSAL")
    existing = conn.execute(
        "SELECT payload_json FROM AuditEvent WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    if existing:
        data = json.loads(existing["payload_json"])
        proposal_id = data["proposal_id"]
        row = conn.execute(
            "SELECT payload_json FROM Proposal WHERE id=?", (proposal_id,)
        ).fetchone()
        conn.close()
        if row is None:
            raise RuntimeError(f"DRAFT_PROPOSAL Dedup ohne Proposal: {proposal_id}")
        return DraftProposalResult(
            proposal_id=proposal_id,
            payload=json.loads(row["payload_json"]),
        )

    provider = get_llm_provider()
    proposal_id, output = run_agent(task=args.task, conn=conn, provider=provider)
    payload = output.model_dump(mode="json")
    append_audit(
        conn,
        dedup_key,
        "DRAFT_PROPOSAL",
        None,
        0,
        {"proposal_id": proposal_id, "task": args.task},
    )
    conn.close()
    return DraftProposalResult(proposal_id=proposal_id, payload=payload)


@_activity.defn(name="create_request")
async def activity_create_request(args: CreateRequestInput) -> CreateRequestResult:
    import subprocess as _sp

    conn = get_db(args.db_path)
    init_schema(conn)

    row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=?", (args.proposal_id,)
    ).fetchone()
    payload = json.loads(row["payload_json"])

    head = _sp.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.repo_path
    ).decode().strip()

    request_id = insert_request(
        conn,
        proposal_id=args.proposal_id,
        proposal_version=1,
        payload=payload,
        expected_revision=head,
        workflow_id=args.workflow_id,
    )
    req_row = conn.execute(
        "SELECT request_hash FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()
    request_hash = req_row["request_hash"]

    append_audit(
        conn,
        _dedup(args.workflow_id, args.step, "CREATE_REQUEST"),
        "CREATE_REQUEST",
        None,
        0,
        {"request_id": request_id},
    )
    conn.close()
    return CreateRequestResult(
        request_id=request_id,
        request_hash=request_hash,
        expected_revision=head,
    )


@_activity.defn(name="mark_request_expired")
async def activity_mark_request_expired(args: MarkRequestExpiredInput) -> None:
    conn = get_db(args.db_path)
    init_schema(conn)
    conn.execute(
        "UPDATE ExecutionRequest SET status='EXPIRED' WHERE id=?",
        (args.request_id,),
    )
    conn.commit()
    append_audit(
        conn,
        _dedup(args.workflow_id, args.step, "EXPIRED"),
        "EXPIRED",
        None,
        0,
        {"request_id": args.request_id},
    )
    conn.close()


@_activity.defn(name="begin_execution")
async def activity_begin_execution(args: BeginExecutionInput) -> str:
    """
    Gibt die execution_id zurück. Gibt "" zurück, wenn die Authorization
    abgelehnt wird (fremde/verbrauchte Auth). Kein Raise – Workflow prüft den Wert.
    """
    conn = get_db(args.db_path)
    init_schema(conn)
    try:
        execution_id = _begin_execution(
            conn,
            request_id=args.request_id,
            auth_id=args.auth_id,
            idempotency_key=args.idempotency_key,
            repo_path=args.repo_path,
        )
    except AuthorizationError:
        conn.close()
        return ""
    conn.close()
    return execution_id


@_activity.defn(name="apply_write")
async def activity_apply_write(args: ApplyWriteInput) -> None:
    conn = get_db(args.db_path)
    init_schema(conn)
    adapter = GitProjectStoreAdapter(Path(args.repo_path), conn)
    adapter.apply(
        payload=args.payload,
        expected_revision=args.expected_revision,
        idempotency_key=args.idempotency_key,
        execution_id=args.execution_id,
    )
    conn.close()


@_activity.defn(name="verify_write")
async def activity_verify_write(args: VerifyWriteInput) -> None:
    """Liest Execution-Zustand; Tamperung wird durch reconcile abgefangen."""
    conn = get_db(args.db_path)
    init_schema(conn)
    conn.execute(
        "SELECT status FROM Execution WHERE id=?", (args.execution_id,)
    ).fetchone()
    conn.close()


@_activity.defn(name="reconcile")
async def activity_reconcile(args: ReconcileInput) -> None:
    """
    Zwei Aufgaben:
    1. APPLIED-Executions auf Tamperung prüfen → DIVERGENT → ESCALATED
       (maybe_crash("after_reconcile_transition") feuert im Kernel nach dem Commit)
    2. Verbliebene STARTED-Executions schließen (erschöpfte apply_write-Retries)
    """
    conn = get_db(args.db_path)
    init_schema(conn)
    adapter = GitProjectStoreAdapter(Path(args.repo_path), conn)

    adapter.reconcile_applied_executions()

    started = conn.execute(
        "SELECT id FROM Execution WHERE status='STARTED'"
    ).fetchall()
    for row in started:
        exec_id = row["id"]
        try:
            transition(
                conn,
                exec_id,
                ExecutionStatus.FAILED,
                "apply_write erschöpft: kein Commit",
            )
        except TransitionError:
            try:
                fail_revision_conflict(conn, exec_id)
            except TransitionError:
                pass

    conn.close()


@_activity.defn(name="read_execution_status")
async def activity_read_execution_status(args: ReadExecutionStatusInput) -> str:
    conn = get_db(args.db_path)
    init_schema(conn)
    row = conn.execute(
        "SELECT status FROM Execution WHERE id=?", (args.execution_id,)
    ).fetchone()
    conn.close()
    return row["status"] if row else "UNKNOWN"


ACTIVITIES = [
    activity_draft_proposal,
    activity_create_request,
    activity_mark_request_expired,
    activity_begin_execution,
    activity_apply_write,
    activity_verify_write,
    activity_reconcile,
    activity_read_execution_status,
]

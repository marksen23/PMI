"""Activity-Wrapper um den PMI-Kern für den InitiationWorkflow."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import temporalio.activity as _activity

from pmi.agent import FakeLLMProvider, LLMProvider, generate_proposal
from pmi.db import (
    append_audit,
    get_db,
    init_schema,
    insert_proposal,
    insert_request,
)
from pmi.fault_inject import maybe_crash
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
    """FakeLLM nur mit nicht-leerem JSON und (pytest oder PMI_ALLOW_FAKE_LLM=1)."""
    fake_json = os.environ.get("PMI_FAKE_LLM_JSON", "")
    if fake_json:
        in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        allow_fake = os.environ.get("PMI_ALLOW_FAKE_LLM") == "1"
        if in_pytest or allow_fake:
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
    proposal_version: int = 1
    payload: dict | None = None


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

def _load_complete_draft(conn, dedup_key: str) -> DraftProposalResult | None:
    existing = conn.execute(
        "SELECT payload_json FROM AuditEvent WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    if not existing:
        return None
    data = json.loads(existing["payload_json"])
    proposal_id = data["proposal_id"]
    row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=? "
        "ORDER BY version DESC LIMIT 1",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"DRAFT_PROPOSAL Dedup ohne Proposal: {proposal_id}")
    return DraftProposalResult(
        proposal_id=proposal_id,
        payload=json.loads(row["payload_json"]),
    )


def _load_staging(conn, staging_key: str) -> dict | None:
    row = conn.execute(
        "SELECT payload_json FROM AuditEvent WHERE dedup_key=?", (staging_key,)
    ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def _persist_draft_tx(
    conn,
    *,
    staging: dict,
    dedup_key: str,
    task: str,
) -> None:
    """A1: eine Transaktion für Proposal + AGENT_PROPOSAL_GENERATED + DRAFT_PROPOSAL."""
    proposal_id = staging["proposal_id"]
    payload = staging["payload"]
    agent_key = f"agent_proposal:{proposal_id}"

    conn.execute("BEGIN")
    try:
        if not conn.execute(
            "SELECT 1 FROM Proposal WHERE id=?", (proposal_id,)
        ).fetchone():
            insert_proposal(
                conn,
                payload=payload,
                proposed_by="agent:llm",
                proposal_id=proposal_id,
                commit=False,
            )
        maybe_crash("after_draft_proposal_insert")

        if not conn.execute(
            "SELECT 1 FROM AuditEvent WHERE dedup_key=?", (agent_key,)
        ).fetchone():
            append_audit(
                conn,
                dedup_key=agent_key,
                event_type="AGENT_PROPOSAL_GENERATED",
                execution_id=None,
                seq=0,
                payload={
                    "proposal_id": proposal_id,
                    "model": staging["model"],
                    "version": staging["version"],
                    "input_hash": staging["input_hash"],
                    "output_hash": staging["output_hash"],
                    "validated_output_hash": staging["validated_output_hash"],
                    "proposal_input_hash": staging["proposal_input_hash"],
                    "draft_dedup_key": dedup_key,
                },
                commit=False,
            )
        maybe_crash("after_draft_agent_audit")

        append_audit(
            conn,
            dedup_key=dedup_key,
            event_type="DRAFT_PROPOSAL",
            execution_id=None,
            seq=0,
            payload={"proposal_id": proposal_id, "task": task},
            commit=False,
        )
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK")
        raise


@_activity.defn(name="draft_proposal")
async def activity_draft_proposal(args: DraftProposalInput) -> DraftProposalResult:
    conn = get_db(args.db_path)
    init_schema(conn)
    dedup_key = _dedup(args.workflow_id, args.step, "DRAFT_PROPOSAL")
    staging_key = _dedup(args.workflow_id, args.step, "DRAFT_STAGING")

    complete = _load_complete_draft(conn, dedup_key)
    if complete is not None:
        conn.close()
        return complete

    staging = _load_staging(conn, staging_key)
    if staging is None:
        # A1: LLM + Validierung ausserhalb der DB-TX
        provider = get_llm_provider()
        generated = generate_proposal(args.task, provider)
        staging = {
            "proposal_id": generated.proposal_id,
            "payload": generated.payload,
            "model": generated.model,
            "version": generated.version,
            "input_hash": generated.input_hash,
            "output_hash": generated.output_hash,
            "validated_output_hash": generated.validated_output_hash,
            "proposal_input_hash": generated.proposal_input_hash,
            "task": args.task,
        }
        # Staging committed vor der TX: Retry nach Kill ohne zweiten LLM-Aufruf
        append_audit(
            conn,
            dedup_key=staging_key,
            event_type="DRAFT_STAGING",
            execution_id=None,
            seq=0,
            payload=staging,
            commit=True,
        )

    _persist_draft_tx(
        conn,
        staging=staging,
        dedup_key=dedup_key,
        task=args.task,
    )
    result = DraftProposalResult(
        proposal_id=staging["proposal_id"],
        payload=staging["payload"],
    )
    conn.close()
    return result


@dataclass
class ApplyAnswersInput:
    db_path: str
    proposal_id: str
    answers: list[str]
    person: str
    step: int
    workflow_id: str


@dataclass
class ApplyAnswersResult:
    proposal_id: str
    version: int
    payload: dict


@_activity.defn(name="apply_answers")
async def activity_apply_answers(args: ApplyAnswersInput) -> ApplyAnswersResult:
    from temporalio.exceptions import ApplicationError

    from pmi.answers import apply_answers

    conn = get_db(args.db_path)
    init_schema(conn)
    try:
        result = apply_answers(
            conn,
            proposal_id=args.proposal_id,
            answers=list(args.answers),
            person=args.person,
        )
    except ValueError as exc:
        conn.close()
        # Business-Regel (Person/D1/Fragen) – nicht retryen (Befund 6)
        raise ApplicationError(str(exc), non_retryable=True) from exc
    row = conn.execute(
        "SELECT payload_json FROM Proposal WHERE id=? AND version=?",
        (args.proposal_id, result["version"]),
    ).fetchone()
    append_audit(
        conn,
        _dedup(args.workflow_id, args.step, "ANSWERS_APPLIED"),
        "ANSWERS_APPLIED",
        None,
        0,
        {
            "proposal_id": args.proposal_id,
            "version": result["version"],
        },
    )
    conn.close()
    return ApplyAnswersResult(
        proposal_id=args.proposal_id,
        version=int(result["version"]),
        payload=json.loads(row["payload_json"]),
    )


@_activity.defn(name="create_request")
async def activity_create_request(args: CreateRequestInput) -> CreateRequestResult:
    import subprocess as _sp

    conn = get_db(args.db_path)
    init_schema(conn)

    head = _sp.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.repo_path
    ).decode().strip()

    # BEGIN IMMEDIATE: Race mit apply_answers → eine konsistente Welt (D1 / Befund 3)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT payload_json, version FROM Proposal WHERE id=? "
            "ORDER BY version DESC LIMIT 1",
            (args.proposal_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        proposal_version = int(row["version"])

        request_id = insert_request(
            conn,
            proposal_id=args.proposal_id,
            proposal_version=proposal_version,
            payload=payload,
            expected_revision=head,
            workflow_id=args.workflow_id,
            commit=False,
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
            commit=False,
        )
        conn.commit()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        raise

    conn.close()
    return CreateRequestResult(
        request_id=request_id,
        request_hash=request_hash,
        expected_revision=head,
        proposal_version=proposal_version,
        payload=payload,
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
    activity_apply_answers,
    activity_create_request,
    activity_mark_request_expired,
    activity_begin_execution,
    activity_apply_write,
    activity_verify_write,
    activity_reconcile,
    activity_read_execution_status,
]

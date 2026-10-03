"""InitiationWorkflow – deterministischer 10-Schritte-Workflow."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    import config as _config

    from pmi.temporal.activities import (
        ApplyAnswersInput,
        ApplyAnswersResult,
        ApplyWriteInput,
        BeginExecutionInput,
        CreateRequestInput,
        CreateRequestResult,
        DraftProposalInput,
        DraftProposalResult,
        MarkRequestExpiredInput,
        ReadExecutionStatusInput,
        ReconcileInput,
        VerifyWriteInput,
        activity_apply_answers,
        activity_apply_write,
        activity_begin_execution,
        activity_create_request,
        activity_draft_proposal,
        activity_mark_request_expired,
        activity_read_execution_status,
        activity_reconcile,
        activity_verify_write,
    )

    _DRAFT_START_TO_CLOSE = _config.DRAFT_PROPOSAL_START_TO_CLOSE_SECONDS
    _DRAFT_MAX_ATTEMPTS = _config.DRAFT_PROPOSAL_MAXIMUM_ATTEMPTS


@dataclass
class WorkflowInput:
    task: str
    db_path: str
    repo_path: str
    approval_timeout_seconds: int


@dataclass
class WorkflowResult:
    status: str


_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1), backoff_coefficient=2.0)
_DRAFT_RETRY = RetryPolicy(
    maximum_attempts=_DRAFT_MAX_ATTEMPTS,
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
)


@workflow.defn
class InitiationWorkflow:
    def __init__(self) -> None:
        self._auth_id: str | None = None
        self._answers: dict | None = None

    @workflow.signal
    def authorized(self, auth_id: str) -> None:
        self._auth_id = auth_id

    @workflow.signal
    def answers(self, payload: dict) -> None:
        """A3: Signal mit answers + person; Workflow wartet vor create_request (D1)."""
        self._answers = payload

    @workflow.run
    async def run(self, args: WorkflowInput) -> WorkflowResult:
        wf_id = workflow.info().workflow_id

        # Schritt 1: draft_proposal
        draft: DraftProposalResult = await workflow.execute_activity(
            activity_draft_proposal,
            DraftProposalInput(
                task=args.task,
                db_path=args.db_path,
                step=1,
                workflow_id=wf_id,
            ),
            start_to_close_timeout=timedelta(seconds=_DRAFT_START_TO_CLOSE),
            retry_policy=_DRAFT_RETRY,
        )

        payload = draft.payload
        questions = list((payload.get("steckbrief") or {}).get("offene_fragen") or [])

        # Schritt 2: answers nur wenn offene_fragen nicht leer (A3/D1)
        if questions:
            try:
                await workflow.wait_condition(
                    lambda: self._answers is not None,
                    timeout=timedelta(seconds=args.approval_timeout_seconds),
                )
            except Exception:
                # Timeout ohne Signal → EXPIRED, kein ExecutionRequest (Befund 2)
                return WorkflowResult(status="EXPIRED")
            answers_payload = self._answers or {}
            applied: ApplyAnswersResult = await workflow.execute_activity(
                activity_apply_answers,
                ApplyAnswersInput(
                    db_path=args.db_path,
                    proposal_id=draft.proposal_id,
                    answers=list(answers_payload.get("answers") or []),
                    person=str(answers_payload.get("person") or ""),
                    step=2,
                    workflow_id=wf_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_RETRY,
            )
            payload = applied.payload

        # Schritt 3: create_request (neueste Version / neuer Hash)
        create: CreateRequestResult = await workflow.execute_activity(
            activity_create_request,
            CreateRequestInput(
                db_path=args.db_path,
                repo_path=args.repo_path,
                proposal_id=draft.proposal_id,
                step=3,
                workflow_id=wf_id,
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_RETRY,
        )
        if create.payload is not None:
            payload = create.payload

        # Schritt 4: Warte auf authorized-Signal oder Timeout
        try:
            await workflow.wait_condition(
                lambda: self._auth_id is not None,
                timeout=timedelta(seconds=args.approval_timeout_seconds),
            )
        except Exception:
            await workflow.execute_activity(
                activity_mark_request_expired,
                MarkRequestExpiredInput(
                    db_path=args.db_path,
                    request_id=create.request_id,
                    step=5,
                    workflow_id=wf_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_RETRY,
            )
            return WorkflowResult(status="EXPIRED")

        auth_id = self._auth_id

        # Schritt 6: begin_execution
        execution_id: str = await workflow.execute_activity(
            activity_begin_execution,
            BeginExecutionInput(
                db_path=args.db_path,
                repo_path=args.repo_path,
                request_id=create.request_id,
                auth_id=auth_id,
                idempotency_key=create.request_hash,
                step=6,
                workflow_id=wf_id,
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_RETRY,
        )

        if not execution_id:
            return WorkflowResult(status="AUTH_REJECTED")

        # Schritt 7: apply_write (max. 5 Versuche)
        apply_succeeded = True
        try:
            await workflow.execute_activity(
                activity_apply_write,
                ApplyWriteInput(
                    db_path=args.db_path,
                    repo_path=args.repo_path,
                    payload=payload,
                    expected_revision=create.expected_revision,
                    idempotency_key=create.request_hash,
                    execution_id=execution_id,
                    step=7,
                    workflow_id=wf_id,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RETRY,
            )
        except ActivityError:
            apply_succeeded = False

        # Schritt 8: verify_write (nur wenn apply erfolgreich)
        if apply_succeeded:
            await workflow.execute_activity(
                activity_verify_write,
                VerifyWriteInput(
                    db_path=args.db_path,
                    repo_path=args.repo_path,
                    execution_id=execution_id,
                    step=8,
                    workflow_id=wf_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )

        # Schritt 9: reconcile
        await workflow.execute_activity(
            activity_reconcile,
            ReconcileInput(
                db_path=args.db_path,
                repo_path=args.repo_path,
                step=9,
                workflow_id=wf_id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )

        # Schritt 10: Status aus der Kontroll-DB lesen (K3)
        db_status: str = await workflow.execute_activity(
            activity_read_execution_status,
            ReadExecutionStatusInput(
                db_path=args.db_path,
                execution_id=execution_id,
                step=10,
                workflow_id=wf_id,
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_RETRY,
        )

        return WorkflowResult(status=db_status)

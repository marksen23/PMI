"""Zustandsmaschine + Übergänge für PMI Governance-Kernel."""
import hashlib
import hmac
import sqlite3
import uuid
from enum import Enum

import config
from pmi.db import append_audit, compute_request_hash
from pmi.fault_inject import maybe_crash


class ExecutionStatus(str, Enum):
    STARTED = "STARTED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    DIVERGENT = "DIVERGENT"
    ESCALATED = "ESCALATED"


class AuthorizationError(Exception):
    pass


class TransitionError(Exception):
    pass


def _verify_signature(request_hash: str, decided_by: str, signature: str) -> bool:
    msg = f"{request_hash}:{decided_by}".encode()
    expected = hmac.new(config.AUTHORIZATION_HMAC_SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def begin_execution(
    conn: sqlite3.Connection,
    request_id: str,
    auth_id: str,
    idempotency_key: str,
    payload_override: dict | None = None,
    repo_path: str | None = None,
) -> str:
    """
    Startet eine Execution. expected_revision wird aus dem ExecutionRequest gelesen –
    der Worker liest HEAD einmalig bei Request-Erstellung, nicht hier.
    repo_path wird in Execution gespeichert, damit transition(FAILED) HEAD prüfen kann.
    """
    maybe_crash("pre_start_persist")

    auth_row = conn.execute(
        "SELECT a.id, a.request_id, a.decided_by, a.expires_at, a.used, a.signature, "
        "       a.request_hash AS approved_hash, "
        "       er.request_hash AS current_hash, er.consumed_by_execution_id, er.expected_revision "
        "FROM Authorization a "
        "JOIN ExecutionRequest er ON er.id = a.request_id "
        "WHERE a.id=?",
        (auth_id,),
    ).fetchone()

    if auth_row is None:
        raise AuthorizationError(f"Authorization {auth_id} nicht gefunden")

    if auth_row["request_id"] != request_id:
        raise AuthorizationError("Authorization gehört zu einem anderen Request")

    approved_hash = auth_row["approved_hash"] or ""
    if approved_hash != auth_row["current_hash"]:
        raise AuthorizationError("HASH_MISMATCH: request_hash nach Freigabe geändert")

    if not _verify_signature(approved_hash, auth_row["decided_by"], auth_row["signature"]):
        raise AuthorizationError("HMAC-Signatur ungültig")

    expired = conn.execute(
        "SELECT 1 FROM Authorization WHERE id=? AND expires_at <= datetime('now')", (auth_id,)
    ).fetchone()
    if expired:
        raise AuthorizationError("Authorization ist abgelaufen")

    if payload_override is not None:
        if compute_request_hash(payload_override) != approved_hash:
            raise AuthorizationError("HASH_MISMATCH: Payload nach Freigabe geändert")

    if auth_row["used"] or auth_row["consumed_by_execution_id"]:
        existing = conn.execute(
            "SELECT id FROM Execution "
            "WHERE idempotency_key=? AND request_id=? AND authorization_id=?",
            (idempotency_key, request_id, auth_id),
        ).fetchone()
        if existing:
            return existing["id"]
        if auth_row["used"]:
            dedup = f"auth_already_used:{auth_id}:{idempotency_key}"
            append_audit(conn, dedup, "AUTHORIZATION_ALREADY_USED", None, 0, {"auth_id": auth_id})
            raise AuthorizationError("Authorization wurde bereits verbraucht")
        raise AuthorizationError("ExecutionRequest bereits verbraucht")

    execution_id = str(uuid.uuid4())
    # expected_revision stammt aus dem ExecutionRequest (festgelegt bei Request-Erstellung)
    initial_revision = auth_row["expected_revision"] or None

    conn.execute("BEGIN")
    try:
        conn.execute("UPDATE Authorization SET used=1 WHERE id=?", (auth_id,))
        conn.execute(
            "UPDATE ExecutionRequest SET consumed_by_execution_id=? WHERE id=?",
            (execution_id, request_id),
        )
        conn.execute(
            "INSERT INTO Execution "
            "(id, request_id, authorization_id, idempotency_key, status, repo_path, expected_revision) "
            "VALUES (?, ?, ?, ?, 'STARTED', ?, ?)",
            (execution_id, request_id, auth_id, idempotency_key, repo_path, initial_revision),
        )
        conn.execute(
            "INSERT INTO AuditEvent (id, dedup_key, event_type, execution_id, seq, payload_json) "
            "VALUES (?, ?, 'STARTED', ?, 0, '{}')",
            (str(uuid.uuid4()), f"started:{execution_id}", execution_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return execution_id


def fail_revision_conflict(conn: sqlite3.Connection, execution_id: str) -> None:
    """
    CAS hat keinen Schreibvorgang zugelassen. Setzt FAILED mit Nachweis.

    transition(FAILED) bleibt bei verändertem HEAD verboten. Diese Funktion
    schreibt FAILED nur, wenn HEAD abweicht und kein Commit den Idempotenzschlüssel trägt.
    """
    row = conn.execute(
        "SELECT status, repo_path, expected_revision, idempotency_key, zero_effect_proof "
        "FROM Execution WHERE id=?",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise TransitionError(f"Execution {execution_id} nicht gefunden")
    if row["status"] == ExecutionStatus.FAILED.value:
        if row["zero_effect_proof"]:
            return
        raise TransitionError("FAILED ohne zero_effect_proof")
    if row["status"] != ExecutionStatus.STARTED.value:
        raise TransitionError(
            f"REVISION_CONFLICT kann Status {row['status']} nicht auf FAILED setzen"
        )
    repo_path = row["repo_path"]
    if not repo_path:
        raise TransitionError("REVISION_CONFLICT ohne repo_path: Null-Effekt nicht prüfbar")

    import subprocess as _sp

    head = _sp.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
    )
    if head.returncode != 0:
        raise TransitionError("git rev-parse fehlgeschlagen")
    current_head = head.stdout.decode().strip()
    expected = row["expected_revision"] or ""
    if current_head == expected:
        raise TransitionError("kein Revisionskonflikt")

    marker = f"pmi-idempotency-key:{row['idempotency_key']}"
    log = _sp.run(
        ["git", "log", "--format=%s", "--all"],
        cwd=repo_path,
        capture_output=True,
    )
    if log.returncode != 0:
        raise TransitionError("git log fehlgeschlagen")
    if any(marker in line for line in log.stdout.decode().splitlines()):
        raise TransitionError("FAILED verboten: Commit mit Idempotenzschlüssel vorhanden")

    proof = (
        f"CAS: HEAD {current_head} weicht von expected_revision {expected} ab, "
        "kein Commit mit Idempotenzschlüssel"
    )
    conn.execute(
        "UPDATE Execution SET status='FAILED', zero_effect_proof=?, finished_at=datetime('now') "
        "WHERE id=? AND status='STARTED'",
        (proof, execution_id),
    )
    append_audit(
        conn,
        f"revision_conflict:{execution_id}",
        "FAILED",
        execution_id,
        0,
        {"zero_effect_proof": proof},
    )
    conn.commit()


def transition(
    conn: sqlite3.Connection,
    execution_id: str,
    to_state: ExecutionStatus,
    zero_effect_proof: str | None = None,
) -> None:
    row = conn.execute(
        "SELECT status, repo_path, expected_revision FROM Execution WHERE id=?",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise TransitionError(f"Execution {execution_id} nicht gefunden")

    current = row["status"]

    if current == ExecutionStatus.APPLIED.value and to_state == ExecutionStatus.FAILED:
        raise TransitionError("APPLIED → FAILED ist verboten")

    if to_state == ExecutionStatus.FAILED:
        if not zero_effect_proof:
            raise TransitionError("FAILED erfordert zero_effect_proof")
        # Prüfe ob HEAD noch dem Anfangszustand entspricht (I3).
        if row["repo_path"] and row["expected_revision"]:
            import subprocess as _sp
            result = _sp.run(
                ["git", "rev-parse", "HEAD"],
                cwd=row["repo_path"],
                capture_output=True,
            )
            if result.returncode == 0:
                current_head = result.stdout.decode().strip()
                if current_head != row["expected_revision"]:
                    raise TransitionError(
                        "FAILED verboten: Store-Revision hat sich seit Anfang geändert "
                        f"(erwartet={row['expected_revision']}, aktuell={current_head})"
                    )

    valid_transitions: dict[str, set[ExecutionStatus]] = {
        ExecutionStatus.STARTED.value: {
            ExecutionStatus.APPLIED,
            ExecutionStatus.FAILED,
            ExecutionStatus.DIVERGENT,
        },
        ExecutionStatus.APPLIED.value: {
            ExecutionStatus.APPLIED,   # idempotent no-op
            ExecutionStatus.DIVERGENT,
        },
        ExecutionStatus.DIVERGENT.value: {
            ExecutionStatus.ESCALATED,
        },
        ExecutionStatus.ESCALATED.value: set(),
        ExecutionStatus.FAILED.value: set(),
    }

    allowed = valid_transitions.get(current, set())
    if to_state not in allowed:
        raise TransitionError(f"Ungültiger Übergang: {current} → {to_state.value}")

    set_parts = ["status=?"]
    params: list = [to_state.value]

    if to_state in (ExecutionStatus.APPLIED, ExecutionStatus.FAILED, ExecutionStatus.ESCALATED):
        set_parts.append("finished_at=datetime('now')")

    if to_state == ExecutionStatus.FAILED and zero_effect_proof:
        set_parts.append("zero_effect_proof=?")
        params.append(zero_effect_proof)

    params.append(execution_id)
    conn.execute(
        f"UPDATE Execution SET {', '.join(set_parts)} WHERE id=?",
        params,
    )

    append_audit(conn, f"transition:{execution_id}:{to_state.value}", to_state.value, execution_id, 0, {})

    if to_state == ExecutionStatus.DIVERGENT:
        conn.execute(
            "UPDATE Execution SET status='ESCALATED', finished_at=datetime('now') WHERE id=?",
            (execution_id,),
        )
        append_audit(conn, f"escalated:{execution_id}", "ESCALATED", execution_id, 1, {})

    conn.commit()

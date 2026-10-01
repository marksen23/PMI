"""Approval-Service: serverseitige Hash-Berechnung + HMAC-Signatur."""
import hashlib
import hmac
import json
import sqlite3
import uuid
from dataclasses import dataclass

import config
from pmi.db import append_audit
from pmi.fault_inject import maybe_crash


@dataclass
class Authorization:
    id: str
    request_id: str
    decided_by: str
    expires_at: str
    signature: str


def _compute_signature(request_hash: str, decided_by: str) -> str:
    msg = f"{request_hash}:{decided_by}".encode()
    return hmac.new(config.AUTHORIZATION_HMAC_SECRET, msg, hashlib.sha256).hexdigest()


def approve(
    conn: sqlite3.Connection,
    request_id: str,
    person: str,
    ttl_seconds: int | None = None,
) -> Authorization:
    if not person or not str(person).strip():
        dedup = f"rejected_invalid_decided_by:{request_id}:{uuid.uuid4()}"
        append_audit(conn, dedup, "REJECTED_INVALID_DECIDED_BY", None, 0, {"request_id": request_id})
        raise ValueError("person darf nicht leer sein; decided_by erfordert human:-Präfix")

    ttl = ttl_seconds if ttl_seconds is not None else config.AUTHORIZATION_TTL_SECONDS
    decided_by = f"human:{person.strip()}"

    row = conn.execute(
        "SELECT request_hash FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"ExecutionRequest {request_id} nicht gefunden")

    request_hash = row["request_hash"]
    signature = _compute_signature(request_hash, decided_by)

    auth_id = str(uuid.uuid4())
    maybe_crash("mid_authorization")
    conn.execute(
        "INSERT INTO Authorization (id, request_id, decided_by, expires_at, used, signature) "
        "VALUES (?, ?, ?, datetime('now', ? || ' seconds'), 0, ?)",
        (auth_id, request_id, decided_by, str(ttl), signature),
    )
    conn.commit()

    row2 = conn.execute(
        "SELECT expires_at FROM Authorization WHERE id=?", (auth_id,)
    ).fetchone()
    return Authorization(
        id=auth_id,
        request_id=request_id,
        decided_by=decided_by,
        expires_at=row2["expires_at"],
        signature=signature,
    )

"""SQLite-Schema und DB-Zugriff für PMI Governance-Kernel."""
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import config


def get_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Proposal (
            id          TEXT PRIMARY KEY,
            version     INTEGER NOT NULL DEFAULT 1,
            proposed_by TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ExecutionRequest (
            id                    TEXT PRIMARY KEY,
            proposal_id           TEXT NOT NULL,
            proposal_version      INTEGER NOT NULL,
            request_hash          TEXT NOT NULL,
            expected_revision     TEXT NOT NULL DEFAULT '',
            created_at            TEXT NOT NULL DEFAULT (datetime('now')),
            consumed_by_execution_id TEXT,
            FOREIGN KEY (proposal_id) REFERENCES Proposal(id)
        );

        CREATE TABLE IF NOT EXISTS Authorization (
            id          TEXT PRIMARY KEY,
            request_id  TEXT NOT NULL,
            decided_by  TEXT NOT NULL,
            granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL,
            used        INTEGER NOT NULL DEFAULT 0,
            signature   TEXT NOT NULL,
            FOREIGN KEY (request_id) REFERENCES ExecutionRequest(id)
        );

        CREATE TABLE IF NOT EXISTS Execution (
            id                    TEXT PRIMARY KEY,
            request_id            TEXT NOT NULL,
            authorization_id      TEXT NOT NULL,
            idempotency_key       TEXT NOT NULL,
            status                TEXT NOT NULL DEFAULT 'STARTED',
            started_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
            zero_effect_proof     TEXT,
            finished_at           TEXT,
            repo_path             TEXT,
            expected_revision     TEXT,
            FOREIGN KEY (request_id) REFERENCES ExecutionRequest(id),
            FOREIGN KEY (authorization_id) REFERENCES Authorization(id)
        );

        CREATE TABLE IF NOT EXISTS AuditEvent (
            id           TEXT PRIMARY KEY,
            dedup_key    TEXT UNIQUE,
            event_type   TEXT NOT NULL,
            execution_id TEXT,
            seq          INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=config.CANONICAL_JSON_SORT_KEYS,
        separators=config.CANONICAL_JSON_SEPARATORS,
    ).encode()


def compute_request_hash(payload: dict, expected_revision: str = "") -> str:
    """Hash über Payload + expected_revision – bindet den Request an einen konkreten Store-Stand."""
    canonical = _canonical_json(payload)
    rev_bytes = (expected_revision or "").encode()
    return hashlib.sha256(canonical + b"|" + rev_bytes).hexdigest()


def insert_proposal(conn: sqlite3.Connection, payload: dict, proposed_by: str) -> str:
    proposal_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO Proposal (id, proposed_by, payload_json) VALUES (?, ?, ?)",
        (proposal_id, proposed_by, json.dumps(payload)),
    )
    conn.commit()
    return proposal_id


def insert_request(
    conn: sqlite3.Connection,
    proposal_id: str,
    proposal_version: int,
    payload: dict,
    expected_revision: str = "",
) -> str:
    """Erstellt einen ExecutionRequest. expected_revision = aktueller Store-Stand (git HEAD)."""
    request_id = str(uuid.uuid4())
    request_hash = compute_request_hash(payload, expected_revision)
    conn.execute(
        "INSERT INTO ExecutionRequest "
        "(id, proposal_id, proposal_version, request_hash, expected_revision) "
        "VALUES (?, ?, ?, ?, ?)",
        (request_id, proposal_id, proposal_version, request_hash, expected_revision),
    )
    conn.commit()
    return request_id


def append_audit(
    conn: sqlite3.Connection,
    dedup_key: str,
    event_type: str,
    execution_id: str | None,
    seq: int,
    payload: dict,
) -> None:
    event_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO AuditEvent (id, dedup_key, event_type, execution_id, seq, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, dedup_key, event_type, execution_id, seq, json.dumps(payload)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # dedup_key bereits vorhanden – idempotent

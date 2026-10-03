"""SQLite-Schema und DB-Zugriff für PMI Governance-Kernel."""
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

def get_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Proposal (
            id          TEXT NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,
            proposed_by TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            diff_json   TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (id, version)
        );

        CREATE TABLE IF NOT EXISTS ExecutionRequest (
            id                    TEXT PRIMARY KEY,
            proposal_id           TEXT NOT NULL,
            proposal_version      INTEGER NOT NULL,
            request_hash          TEXT NOT NULL,
            expected_revision     TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT 'OPEN',
            workflow_id           TEXT,
            created_at            TEXT NOT NULL DEFAULT (datetime('now')),
            consumed_by_execution_id TEXT,
            FOREIGN KEY (proposal_id, proposal_version)
                REFERENCES Proposal(id, version)
        );

        CREATE TABLE IF NOT EXISTS Authorization (
            id          TEXT PRIMARY KEY,
            request_id  TEXT NOT NULL,
            decided_by  TEXT NOT NULL,
            granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL,
            used        INTEGER NOT NULL DEFAULT 0,
            signature   TEXT NOT NULL,
            request_hash TEXT NOT NULL DEFAULT '',
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
    auth_columns = [row[1] for row in conn.execute("PRAGMA table_info(Authorization)")]
    if "request_hash" not in auth_columns:
        conn.execute(
            "ALTER TABLE Authorization ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''"
        )
    req_columns = [row[1] for row in conn.execute("PRAGMA table_info(ExecutionRequest)")]
    if "status" not in req_columns:
        conn.execute(
            "ALTER TABLE ExecutionRequest ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'"
        )
    if "workflow_id" not in req_columns:
        conn.execute("ALTER TABLE ExecutionRequest ADD COLUMN workflow_id TEXT")
    prop_columns = [row[1] for row in conn.execute("PRAGMA table_info(Proposal)")]
    if "diff_json" not in prop_columns:
        conn.execute(
            "ALTER TABLE Proposal ADD COLUMN diff_json TEXT NOT NULL DEFAULT ''"
        )
    _migrate_proposal_composite_pk(conn)
    conn.commit()


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    if row is None or row["sql"] is None:
        return ""
    return " ".join(row["sql"].split())


def _er_fk_ok(er_sql: str) -> bool:
    return (
        "REFERENCES Proposal(id, version)" in er_sql
        or "REFERENCES Proposal (id, version)" in er_sql
    )


def _recreate_execution_request_with_fk(conn: sqlite3.Connection) -> None:
    """ExecutionRequest neu mit FK auf Proposal(id, version); Daten behalten."""
    conn.execute(
        """
        CREATE TABLE ExecutionRequest__k4 (
            id                    TEXT PRIMARY KEY,
            proposal_id           TEXT NOT NULL,
            proposal_version      INTEGER NOT NULL,
            request_hash          TEXT NOT NULL,
            expected_revision     TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT 'OPEN',
            workflow_id           TEXT,
            created_at            TEXT NOT NULL DEFAULT (datetime('now')),
            consumed_by_execution_id TEXT,
            FOREIGN KEY (proposal_id, proposal_version)
                REFERENCES Proposal(id, version)
        )
        """
    )
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(ExecutionRequest)").fetchall()
    }
    # Spalten der Legacy-Tabelle auf Zielschema abbilden
    select_bits = [
        "id",
        "proposal_id",
        "proposal_version",
        "request_hash",
        "COALESCE(expected_revision, '')" if "expected_revision" in cols else "''",
        "COALESCE(status, 'OPEN')" if "status" in cols else "'OPEN'",
        "workflow_id" if "workflow_id" in cols else "NULL",
        "COALESCE(created_at, datetime('now'))" if "created_at" in cols else "datetime('now')",
        "consumed_by_execution_id" if "consumed_by_execution_id" in cols else "NULL",
    ]
    conn.execute(
        "INSERT INTO ExecutionRequest__k4 ("
        "id, proposal_id, proposal_version, request_hash, expected_revision, "
        "status, workflow_id, created_at, consumed_by_execution_id) "
        f"SELECT {', '.join(select_bits)} FROM ExecutionRequest"
    )
    conn.execute("DROP TABLE ExecutionRequest")
    conn.execute("ALTER TABLE ExecutionRequest__k4 RENAME TO ExecutionRequest")


def _migrate_proposal_composite_pk(conn: sqlite3.Connection) -> None:
    """Alte DBs: Proposal.id alleiniger PK → (id, version); ER-FK atomar mitziehen.

    Wiederanlauf: liegengebliebene Proposal__k4 wird verworfen und Migration neu gefahren.
    """
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "Proposal" not in names and "Proposal__k4" not in names:
        return

    prop_sql = _table_sql(conn, "Proposal") if "Proposal" in names else ""
    has_composite = "PRIMARY KEY (id, version)" in prop_sql
    has_k4 = "Proposal__k4" in names
    er_sql = _table_sql(conn, "ExecutionRequest") if "ExecutionRequest" in names else ""
    er_needs_fk = bool(er_sql) and not _er_fk_ok(er_sql)

    if has_composite and not has_k4 and not er_needs_fk:
        return

    # FK aus – sonst scheitert DROP Proposal bei referenzierenden Requests
    conn.execute("PRAGMA foreign_keys=OFF")
    # Sicherstellen, dass keine implizite TX offen ist
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if has_k4:
            conn.execute("DROP TABLE Proposal__k4")

        if not has_composite and "Proposal" in names:
            conn.execute(
                """
                CREATE TABLE Proposal__k4 (
                    id           TEXT NOT NULL,
                    version      INTEGER NOT NULL DEFAULT 1,
                    proposed_by  TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    diff_json    TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (id, version)
                )
                """
            )
            prop_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(Proposal)").fetchall()
            }
            diff_expr = (
                "COALESCE(diff_json, '')" if "diff_json" in prop_cols else "''"
            )
            created_expr = (
                "COALESCE(created_at, datetime('now'))"
                if "created_at" in prop_cols
                else "datetime('now')"
            )
            version_expr = (
                "COALESCE(version, 1)" if "version" in prop_cols else "1"
            )
            conn.execute(
                "INSERT INTO Proposal__k4 "
                "(id, version, proposed_by, payload_json, diff_json, created_at) "
                f"SELECT id, {version_expr}, proposed_by, payload_json, "
                f"{diff_expr}, {created_expr} FROM Proposal"
            )
            conn.execute("DROP TABLE Proposal")
            conn.execute("ALTER TABLE Proposal__k4 RENAME TO Proposal")

        # ER-FK nachziehen (auch wenn Proposal schon composite war)
        er_sql_now = _table_sql(conn, "ExecutionRequest")
        if er_sql_now and not _er_fk_ok(er_sql_now):
            _recreate_execution_request_with_fk(conn)

        conn.commit()
    except Exception:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.execute("PRAGMA foreign_keys=ON")


def _check_no_floats(obj: object, path: str = "") -> None:
    if isinstance(obj, float):
        raise ValueError(f"Float-Werte im Payload verboten (kein RFC-8785-konformes JSON): {path}={obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_no_floats(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_no_floats(v, f"{path}[{i}]")


def _canonical_json(payload: dict) -> bytes:
    """Kanonisches JSON: sortierte Schlüssel, kein Whitespace, UTF-8. Floats verboten."""
    _check_no_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_request_hash(payload: dict, expected_revision: str = "") -> str:
    """Hash über Payload + expected_revision – bindet den Request an einen konkreten Store-Stand."""
    canonical = _canonical_json(payload)
    rev_bytes = (expected_revision or "").encode()
    return hashlib.sha256(canonical + b"|" + rev_bytes).hexdigest()


def insert_proposal(
    conn: sqlite3.Connection,
    payload: dict,
    proposed_by: str,
    *,
    proposal_id: str | None = None,
    version: int = 1,
    diff_json: str = "",
    commit: bool = True,
) -> str:
    pid = proposal_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json, diff_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, version, proposed_by, json.dumps(payload), diff_json),
    )
    if commit:
        conn.commit()
    return pid


def insert_request(
    conn: sqlite3.Connection,
    proposal_id: str,
    proposal_version: int,
    payload: dict,
    expected_revision: str = "",
    workflow_id: str | None = None,
    *,
    commit: bool = True,
) -> str:
    """Erstellt einen ExecutionRequest. expected_revision = aktueller Store-Stand (git HEAD).

    Gleiche proposal_id, proposal_version, request_hash und expected_revision
    liefern den vorhandenen Request zurück. workflow_id wird im selben INSERT
    geschrieben. Ein vorhandener Wert wird nicht überschrieben; NULL wird nachgezogen.
    """
    revision = expected_revision or ""
    request_hash = compute_request_hash(payload, revision)
    existing = conn.execute(
        "SELECT id, workflow_id FROM ExecutionRequest "
        "WHERE proposal_id=? AND proposal_version=? AND request_hash=? AND expected_revision=?",
        (proposal_id, proposal_version, request_hash, revision),
    ).fetchone()
    if existing:
        if workflow_id and not existing["workflow_id"]:
            conn.execute(
                "UPDATE ExecutionRequest SET workflow_id=? WHERE id=? AND workflow_id IS NULL",
                (workflow_id, existing["id"]),
            )
            if commit:
                conn.commit()
        return existing["id"]
    request_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO ExecutionRequest "
        "(id, proposal_id, proposal_version, request_hash, expected_revision, workflow_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (request_id, proposal_id, proposal_version, request_hash, revision, workflow_id),
    )
    if commit:
        conn.commit()
    return request_id


def append_audit(
    conn: sqlite3.Connection,
    dedup_key: str,
    event_type: str,
    execution_id: str | None,
    seq: int,
    payload: dict,
    *,
    commit: bool = True,
) -> None:
    event_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO AuditEvent (id, dedup_key, event_type, execution_id, seq, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, dedup_key, event_type, execution_id, seq, json.dumps(payload)),
        )
        if commit:
            conn.commit()
    except sqlite3.IntegrityError:
        pass  # dedup_key bereits vorhanden – idempotent

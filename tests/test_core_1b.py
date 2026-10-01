"""
Auftrag 1b – Tests vor der Kernkorrektur.

Bestehende Tests bleiben unverändert. Diese Datei prüft Lücken, die die
Suite bisher nicht trifft. Erwarteter Stand vor der Implementierung: die
vier Fälle aus dem Auftrag sind rot. Der Dedup-Test und die Abgrenzung
„andere Revision → neuer Request" sind bereits grün und dienen der Gegenprobe.

Keine Mocks. Echte SQLite-DB, echtes git-Repo.
"""
import subprocess
import uuid
from pathlib import Path

import pytest

from pmi.approval import approve
from pmi.db import append_audit, compute_request_hash, get_db, init_schema, insert_proposal, insert_request
from pmi.git_adapter import ApplyResult, GitProjectStoreAdapter
from pmi.state_machine import begin_execution


SAMPLE_PAYLOAD = {"action": "touch_file", "path": "out.txt", "scope": "local"}


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo,
    ).decode().strip()


def _commit_count(repo: Path) -> int:
    return int(subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo,
    ).decode().strip())


def _setup_request(conn, payload=None, expected_revision="") -> tuple[str, str]:
    payload = payload or SAMPLE_PAYLOAD
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    request_id = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1, payload=payload,
        expected_revision=expected_revision,
    )
    return proposal_id, request_id


def test_foreign_authorization_does_not_consume_other_request(tmp_db_path, tmp_git_repo):
    """Authorization von Request A darf Request B nicht starten und nicht verbrauchen."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    head = _git_head(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    _, request_a = _setup_request(conn, expected_revision=head)
    _, request_b = _setup_request(
        conn,
        payload={"action": "touch_file", "path": "other.txt", "scope": "local"},
        expected_revision=head,
    )
    auth_a = approve(conn, request_a, person="alice")

    rejected = False
    try:
        begin_execution(
            conn,
            request_id=request_b,
            auth_id=auth_a.id,
            idempotency_key=str(uuid.uuid4()),
            repo_path=str(tmp_git_repo),
        )
    except Exception:
        rejected = True

    executions = conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0]
    used = conn.execute(
        "SELECT used FROM Authorization WHERE id=?", (auth_a.id,)
    ).fetchone()[0]
    consumed_a = conn.execute(
        "SELECT consumed_by_execution_id FROM ExecutionRequest WHERE id=?",
        (request_a,),
    ).fetchone()[0]
    consumed_b = conn.execute(
        "SELECT consumed_by_execution_id FROM ExecutionRequest WHERE id=?",
        (request_b,),
    ).fetchone()[0]

    assert rejected, "fremde Authorization muss abgelehnt werden"
    assert executions == 0, "keine Execution bei fremder Authorization"
    assert used == 0, "Authorization von A bleibt unverbraucht"
    assert consumed_a is None
    assert consumed_b is None, "Request B darf nicht verbraucht werden"
    assert _commit_count(tmp_git_repo) == commits_before
    assert _git_head(tmp_git_repo) == head


def test_authorization_stores_approved_request_hash(tmp_db_path):
    """Authorization speichert den freigegebenen request_hash und lehnt Abweichung ab."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    _, request_id = _setup_request(conn, expected_revision="rev-1")
    original = conn.execute(
        "SELECT request_hash FROM ExecutionRequest WHERE id=?", (request_id,)
    ).fetchone()[0]
    auth = approve(conn, request_id, person="alice")

    stored = conn.execute(
        "SELECT request_hash FROM Authorization WHERE id=?", (auth.id,)
    ).fetchone()[0]
    assert stored == original

    conn.execute(
        "UPDATE ExecutionRequest SET request_hash=? WHERE id=?",
        ("tampered-hash", request_id),
    )
    with pytest.raises(Exception):
        begin_execution(
            conn,
            request_id=request_id,
            auth_id=auth.id,
            idempotency_key=str(uuid.uuid4()),
        )

    assert conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0] == 0
    still = conn.execute(
        "SELECT request_hash FROM Authorization WHERE id=?", (auth.id,)
    ).fetchone()[0]
    assert still == original


def test_insert_request_same_inputs_returns_existing(tmp_db_path):
    """Zweiter create_request mit denselben Eingaben liefert denselben Request, eine Zeile."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    payload = dict(SAMPLE_PAYLOAD)
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    first = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1,
        payload=payload, expected_revision="rev-1",
    )
    second = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1,
        payload=payload, expected_revision="rev-1",
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM ExecutionRequest WHERE proposal_id=?",
        (proposal_id,),
    ).fetchone()[0]
    assert first == second
    assert count == 1
    assert conn.execute(
        "SELECT request_hash FROM ExecutionRequest WHERE id=?", (first,)
    ).fetchone()[0] == compute_request_hash(payload, "rev-1")


def test_insert_request_different_revision_creates_new(tmp_db_path):
    """Andere expected_revision ist ein anderer Request."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    payload = dict(SAMPLE_PAYLOAD)
    proposal_id = insert_proposal(conn, payload, proposed_by="agent:stub")
    first = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1,
        payload=payload, expected_revision="rev-1",
    )
    second = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1,
        payload=payload, expected_revision="rev-2",
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM ExecutionRequest WHERE proposal_id=?",
        (proposal_id,),
    ).fetchone()[0]
    assert first != second
    assert count == 2


def test_begin_execution_retry_returns_same_execution(tmp_db_path, tmp_git_repo):
    """Gleicher Idempotenzschlüssel nach eigenem Verbrauch liefert dieselbe Execution."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    head = _git_head(tmp_git_repo)
    _, request_id = _setup_request(conn, expected_revision=head)
    auth = approve(conn, request_id, person="alice")
    idem_key = str(uuid.uuid4())

    first = begin_execution(
        conn, request_id=request_id, auth_id=auth.id,
        idempotency_key=idem_key, repo_path=str(tmp_git_repo),
    )
    second = begin_execution(
        conn, request_id=request_id, auth_id=auth.id,
        idempotency_key=idem_key, repo_path=str(tmp_git_repo),
    )

    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM Execution").fetchone()[0] == 1
    assert conn.execute(
        "SELECT used FROM Authorization WHERE id=?", (auth.id,)
    ).fetchone()[0] == 1
    started = conn.execute(
        "SELECT COUNT(*) FROM AuditEvent WHERE event_type='STARTED'"
    ).fetchone()[0]
    assert started == 1
    assert _commit_count(tmp_git_repo) == 1


def test_revision_conflict_fails_with_zero_effect_proof(tmp_db_path, tmp_git_repo):
    """CAS-Konflikt schreibt nicht und setzt FAILED mit zero_effect_proof."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    head = _git_head(tmp_git_repo)
    _, request_id = _setup_request(conn, expected_revision=head)
    auth = approve(conn, request_id, person="alice")
    idem_key = str(uuid.uuid4())
    execution_id = begin_execution(
        conn, request_id=request_id, auth_id=auth.id,
        idempotency_key=idem_key, repo_path=str(tmp_git_repo),
    )

    (tmp_git_repo / "foreign.txt").write_text("external change")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "external"], cwd=tmp_git_repo, check=True, capture_output=True,
    )
    commits_before = _commit_count(tmp_git_repo)

    adapter = GitProjectStoreAdapter(repo_path=tmp_git_repo, db=conn)
    result = adapter.apply(
        payload=SAMPLE_PAYLOAD,
        expected_revision=head,
        idempotency_key=idem_key,
        execution_id=execution_id,
    )

    row = conn.execute(
        "SELECT status, zero_effect_proof FROM Execution WHERE id=?",
        (execution_id,),
    ).fetchone()
    assert result == ApplyResult.REVISION_CONFLICT
    assert row["status"] == "FAILED"
    assert row["zero_effect_proof"]
    assert _commit_count(tmp_git_repo) == commits_before
    assert not (tmp_git_repo / "out.txt").exists()
    assert conn.execute(
        "SELECT used FROM Authorization WHERE id=?", (auth.id,)
    ).fetchone()[0] == 1


def test_append_audit_dedup_keeps_one_row(tmp_db_path):
    """Gleicher dedup_key erzeugt keine zweite Audit-Zeile und überschreibt die erste nicht."""
    conn = get_db(tmp_db_path)
    init_schema(conn)
    append_audit(conn, "same-key", "NOTE", None, 0, {"n": 1})
    append_audit(conn, "same-key", "NOTE", None, 1, {"n": 2})
    rows = conn.execute(
        "SELECT seq, payload_json FROM AuditEvent WHERE dedup_key=?",
        ("same-key",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["seq"] == 0
    assert '"n": 1' in rows[0]["payload_json"] or '"n":1' in rows[0]["payload_json"]

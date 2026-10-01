"""
Crashtests C0–C6.

Mechanismus: Subprozess-Worker (tests/worker_crash.py) mit PMI_FAULT_INJECT=<point>.
Der Worker bricht an dem benannten Fault-Injection-Punkt mit os._exit(1) ab.
Je Szenario 50 Läufe (immer gleicher Fault-Point, deterministisch reproduzierbar).
Danach wird der Zustand geprüft und ggf. ein Retry-Lauf gemacht.

Alle Tests verwenden echte SQLite-DB und echtes git-Repo in tmp.
"""
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

WORKER = Path(__file__).parent / "worker_crash.py"
RUNS = 50


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo,
    ).decode().strip()


def _commit_count(repo: Path) -> int:
    out = subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo,
    ).decode().strip()
    return int(out)


def _run_worker(scenario: str, db_path: Path, repo_path: Path, fault_point: str) -> int:
    env = os.environ.copy()
    env["PMI_FAULT_INJECT"] = fault_point
    result = subprocess.run(
        [sys.executable, str(WORKER), scenario, str(db_path), str(repo_path)],
        env=env,
    )
    return result.returncode


def _run_worker_clean(scenario: str, db_path: Path, repo_path: Path) -> int:
    env = os.environ.copy()
    env.pop("PMI_FAULT_INJECT", None)
    result = subprocess.run(
        [sys.executable, str(WORKER), scenario, str(db_path), str(repo_path)],
        env=env,
    )
    return result.returncode


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    return repo


# ── C0 – Crash vor persistiertem STARTED ─────────────────────────────────────


def test_c0_crash_before_start_persist(tmp_path):
    """
    Fault-Point pre_start_persist: Crash bevor begin_execution() seinen STARTED-Datensatz
    in die DB geschrieben hat.
    Erwartet: kein Execution-Datensatz in DB, keine Repo-Änderung,
    Authorization noch unverbraucht (used=0), Neustart → normaler Ablauf möglich.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c0_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c0_repo_{uuid.uuid4().hex}")
        head_before = _git_head(repo)
        commits_before = _commit_count(repo)

        rc = _run_worker("begin_execution", db_path, repo, fault_point="pre_start_persist")
        assert rc != 0, "Worker muss mit Fehler enden (Fault-Injection)"

        # Repo unverändert
        assert _git_head(repo) == head_before
        assert _commit_count(repo) == commits_before

        # DB-Zustand: kein STARTED, Authorization unverbraucht
        conn = sqlite3.connect(str(db_path))
        started = conn.execute(
            "SELECT COUNT(*) FROM Execution WHERE status='STARTED'"
        ).fetchone()[0]
        assert started == 0, "Kein STARTED-Datensatz nach Crash vor pre_start_persist"

        used = conn.execute(
            "SELECT used FROM Authorization LIMIT 1"
        ).fetchone()
        if used is not None:
            assert used[0] == 0, "Authorization muss nach Crash unverbraucht sein"
        conn.close()

        # Neustart ohne Fault-Injection → normaler Ablauf
        rc_clean = _run_worker_clean("begin_execution", db_path, repo)
        assert rc_clean == 0, "Neustart muss nach C0-Crash erfolgreich sein"


# ── C1 – Crash vor atomarem Rename ───────────────────────────────────────────


def test_c1_crash_before_rename(tmp_path):
    """
    Fault-Point pre_rename: Crash nach Temp-Write, vor atomarem Rename.
    Kein PARTIAL (mit atomarem Rename gibt es kein halbfertiges Rename).
    Erwartet: kein Commit im Repo, beim Retry genau 1 Commit, APPLIED.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c1_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c1_repo_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("adapter_apply", db_path, repo, fault_point="pre_rename")
        assert rc != 0

        # Kein neuer Commit entstanden
        assert _commit_count(repo) == commits_before

        # Retry ohne Fault-Injection → genau 1 Commit
        rc_clean = _run_worker_clean("adapter_apply", db_path, repo)
        assert rc_clean == 0
        assert _commit_count(repo) == commits_before + 1


# ── C2 – Crash nach Commit, vor DB-Ack ───────────────────────────────────────


def test_c2_crash_after_commit_before_db_ack(tmp_path):
    """
    Fault-Point post_commit: Crash nach git-Commit, vor db.update_status().
    Reconciliation findet eigenen Commit anhand Idempotenzschlüssel → ALREADY_APPLIED,
    kein Doppel-Commit, kein DIVERGENT.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c2_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c2_repo_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("adapter_apply", db_path, repo, fault_point="post_commit")
        assert rc != 0

        # Genau 1 Commit im Repo (der wurde vor dem Crash noch gemacht)
        assert _commit_count(repo) == commits_before + 1

        # Retry: Reconciliation erkennt eigenen Commit → ALREADY_APPLIED, kein Doppel-Commit
        rc_clean = _run_worker_clean("adapter_apply", db_path, repo)
        assert rc_clean == 0
        assert _commit_count(repo) == commits_before + 1, "Kein Doppel-Commit nach C2-Retry"

        conn = sqlite3.connect(str(db_path))
        status = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert status is not None
        assert status[0] in ("APPLIED", "ALREADY_APPLIED")
        conn.close()


# ── C3 – Crash während Authorization-Schreibung ───────────────────────────────


def test_c3_crash_mid_authorization(tmp_path):
    """
    Fault-Point mid_authorization: Crash innerhalb der SQLite-Transaktion für Authorization.
    Erwartet: Transaktion zurückgerollt, kein halbfertiger Authorization-Datensatz, DB konsistent.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c3_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c3_repo_{uuid.uuid4().hex}")

        rc = _run_worker("authorization", db_path, repo, fault_point="mid_authorization")
        assert rc != 0

        # DB muss konsistent sein (kein halbfertiger Datensatz)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            pytest.fail(f"DB-Integrität verletzt nach C3-Crash: {exc}")

        auth_count = conn.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0]
        assert auth_count == 0, "Kein halbfertiger Authorization-Datensatz nach Crash"
        conn.close()

        # Neustart → normaler Ablauf
        rc_clean = _run_worker_clean("authorization", db_path, repo)
        assert rc_clean == 0


# ── C4a – Crash in STARTED, nach Rename, vor Commit ──────────────────────────


def test_c4a_crash_after_rename_before_commit(tmp_path):
    """
    Fault-Point post_rename_pre_commit: Rename vollzogen, Commit noch nicht geschrieben.
    Reconciliation: kein Commit mit eigenem Idempotenzschlüssel → Datei bereinigen
    (git checkout/clean nur für eigenen Pfad), danach Retry → genau 1 Commit, APPLIED.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c4a_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c4a_repo_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("adapter_apply", db_path, repo, fault_point="post_rename_pre_commit")
        assert rc != 0

        # Kein Commit (Rename war da, aber kein git-Commit)
        assert _commit_count(repo) == commits_before

        # Retry: Reconciliation räumt die umbenannte Datei auf, dann 1 sauberer Commit
        rc_clean = _run_worker_clean("adapter_apply", db_path, repo)
        assert rc_clean == 0
        assert _commit_count(repo) == commits_before + 1

        conn = sqlite3.connect(str(db_path))
        status = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert status is not None
        assert status[0] == "APPLIED"
        conn.close()


# ── C5 – DIVERGENT bei manipulierter Zieldatei (reconcile_applied_executions) ─


def test_c5_divergent_on_foreign_commit(tmp_path):
    """
    Kein Worker-Crash. Nach eigenem APPLIED-Commit wird die geschriebene Zieldatei
    von einem fremden Commit überschrieben.
    reconcile_applied_executions() erkennt Inhaltsänderung → DIVERGENT → ESCALATED.
    Normaler additiver Commit (neue unabhängige Datei) löst dagegen kein DIVERGENT aus.
    50 Läufe.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c5_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c5_repo_{uuid.uuid4().hex}")

        # Eigenen Commit anlegen → APPLIED
        rc = _run_worker_clean("adapter_apply", db_path, repo)
        assert rc == 0

        # Normaler additiver Commit (andere Datei) → kein DIVERGENT erwartet
        (repo / "unrelated.txt").write_text("unrelated")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "unrelated-change"],
            cwd=repo, check=True, capture_output=True,
        )
        rc_safe = _run_worker_clean("reconcile", db_path, repo)
        assert rc_safe == 0
        conn = sqlite3.connect(str(db_path))
        status_safe = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert status_safe[0] == "APPLIED", (
            f"Normaler Commit darf kein DIVERGENT auslösen, war: {status_safe[0]}"
        )
        conn.close()

        # Zieldatei überschreiben (Manipulation)
        (repo / "worker_output.txt").write_text("tampered content")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "tampered-target-file"],
            cwd=repo, check=True, capture_output=True,
        )

        # Reconciliation-Lauf → Inhaltsänderung erkannt
        rc_reconcile = _run_worker_clean("reconcile", db_path, repo)
        assert rc_reconcile == 0
        conn = sqlite3.connect(str(db_path))
        status = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert status is not None
        assert status[0] in ("DIVERGENT", "ESCALATED"), (
            f"Erwartete DIVERGENT/ESCALATED bei manipulierter Datei, erhalten: {status[0]}"
        )
        conn.close()


# ── C6 – Crash in STARTED, keinerlei Wirkung ─────────────────────────────────


def test_c6_crash_post_start_pre_write(tmp_path):
    """
    Fault-Point post_start_pre_write: STARTED persistiert, aber kein Schreibvorgang im Repo.
    Reconciliation weist Null-Effekt nach (kein Commit, keine Zieldatei)
    → Retry oder FAILED mit zero_effect_proof; niemals ESCALATED.
    """
    for _ in range(RUNS):
        db_path = tmp_path / f"c6_{uuid.uuid4().hex}.db"
        repo = _make_repo(tmp_path, f"c6_repo_{uuid.uuid4().hex}")
        commits_before = _commit_count(repo)

        rc = _run_worker("adapter_apply", db_path, repo, fault_point="post_start_pre_write")
        assert rc != 0

        # Kein Commit (nichts wurde geschrieben)
        assert _commit_count(repo) == commits_before

        # Reconciliation: Null-Effekt → Retry oder FAILED mit Nachweis
        rc_clean = _run_worker_clean("adapter_apply", db_path, repo)
        assert rc_clean == 0

        conn = sqlite3.connect(str(db_path))
        status = conn.execute(
            "SELECT status FROM Execution ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert status is not None
        assert status[0] in ("APPLIED", "FAILED"), (
            f"Nach C6-Crash: erwartet APPLIED oder FAILED (nie ESCALATED), erhalten: {status[0]}"
        )
        if status[0] == "FAILED":
            proof = conn.execute(
                "SELECT zero_effect_proof FROM Execution ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0]
            assert proof, "FAILED muss zero_effect_proof enthalten"
        conn.close()

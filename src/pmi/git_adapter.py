"""Atomarer Git-Write mit CAS und Idempotenz."""
import json
import sqlite3
import subprocess
import tempfile
import uuid
from enum import Enum
from pathlib import Path

from pmi.fault_inject import maybe_crash
from pmi.state_machine import ExecutionStatus, TransitionError, transition


class ApplyResult(str, Enum):
    APPLIED = "APPLIED"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    PARTIAL = "PARTIAL"
    DIVERGENT = "DIVERGENT"


class GitProjectStoreAdapter:
    def __init__(self, repo_path: Path, db: sqlite3.Connection) -> None:
        self.repo_path = Path(repo_path)
        self.db = db

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            check=check,
        )

    def _current_head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.decode().strip()

    def _idempotency_marker(self, key: str) -> str:
        return f"pmi-idempotency-key:{key}"

    def _find_own_commit(self, idempotency_key: str) -> str | None:
        marker = self._idempotency_marker(idempotency_key)
        result = self._git(
            "log", "--format=%H %s", "--all", check=False
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.decode().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and marker in parts[1]:
                return parts[0]
        return None

    def _verify_execution_started(self, execution_id: str) -> None:
        row = self.db.execute(
            "SELECT status FROM Execution WHERE id=? AND status='STARTED'",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Kein STARTED-Datensatz für execution_id={execution_id}. "
                "Adapter-Aufruf ohne gültiges STARTED verboten (I6)."
            )

    def apply(
        self,
        payload: dict,
        expected_revision: str,
        idempotency_key: str,
        execution_id: str,
    ) -> "ApplyResult":
        self._verify_execution_started(execution_id)

        maybe_crash("post_start_pre_write")

        # Idempotenz: eigener Commit bereits vorhanden?
        own_commit = self._find_own_commit(idempotency_key)
        if own_commit:
            current_head = self._current_head()
            if current_head == own_commit:
                try:
                    transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.APPLIED)
                except TransitionError:
                    pass
                return ApplyResult.ALREADY_APPLIED
            else:
                # Eigener Commit da, aber noch ein fremder darüber
                # (eigene Datei könnte unverändert sein – prüft reconcile_applied_executions)
                try:
                    transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.APPLIED)
                except TransitionError:
                    pass
                return ApplyResult.ALREADY_APPLIED

        # CAS: HEAD muss expected_revision entsprechen
        current_head = self._current_head()
        if current_head != expected_revision:
            return ApplyResult.REVISION_CONFLICT

        # Reconciliation: war eine temp-Datei vorhanden (post_rename_pre_commit)?
        self._cleanup_leftover(payload, idempotency_key)

        # Temp-Datei schreiben
        target_path = self.repo_path / str(payload.get("path", "output.txt"))
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            dir=self.repo_path,
            prefix=".pmi_tmp_",
            delete=False,
            mode="w",
        ) as tmp:
            tmp_path = Path(tmp.name)
            content = dict(payload)
            content["_pmi_idempotency_key"] = idempotency_key
            content["_pmi_execution_id"] = execution_id
            json.dump(content, tmp, sort_keys=True, indent=2)

        maybe_crash("pre_rename")

        # Atomares Rename
        tmp_path.rename(target_path)

        maybe_crash("post_rename_pre_commit")

        # git add + commit
        self._git("add", str(target_path.relative_to(self.repo_path)))
        commit_msg = f"{self._idempotency_marker(idempotency_key)}\npmi-execution:{execution_id}"
        self._git("commit", "-m", commit_msg)

        maybe_crash("post_commit")

        try:
            transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.APPLIED)
        except TransitionError:
            pass

        return ApplyResult.APPLIED

    def reconcile_applied_executions(self) -> list[str]:
        """
        Prüft alle APPLIED-Executions: ist eigener Commit noch in der Historie
        und Dateiinhalt unverändert? Bei Abweichung → DIVERGENT → ESCALATED.
        Normale additive Commits (neue Dateien) lösen kein DIVERGENT aus.
        """
        rows = self.db.execute(
            "SELECT e.id, e.idempotency_key, p.payload_json "
            "FROM Execution e "
            "JOIN ExecutionRequest er ON er.id = e.request_id "
            "JOIN Proposal p ON p.id = er.proposal_id "
            "WHERE e.status='APPLIED'"
        ).fetchall()

        diverged = []
        for row in rows:
            execution_id = row["id"]
            idempotency_key = row["idempotency_key"]
            payload = json.loads(row["payload_json"])

            own_commit = self._find_own_commit(idempotency_key)
            if own_commit is None:
                transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.DIVERGENT)
                diverged.append(execution_id)
                continue

            target_path = self.repo_path / str(payload.get("path", "output.txt"))
            rel_path = str(target_path.relative_to(self.repo_path))

            # Dateiinhalt aus eigenem Commit holen
            show_result = self._git("show", f"{own_commit}:{rel_path}", check=False)
            if show_result.returncode != 0:
                transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.DIVERGENT)
                diverged.append(execution_id)
                continue

            if not target_path.exists():
                transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.DIVERGENT)
                diverged.append(execution_id)
                continue

            if target_path.read_bytes() != show_result.stdout:
                transition(self.db, execution_id=execution_id, to_state=ExecutionStatus.DIVERGENT)
                diverged.append(execution_id)

        return diverged

    def _cleanup_leftover(self, payload: dict, idempotency_key: str) -> None:
        """Bereinigt hinterlassene umbenannte Datei von post_rename_pre_commit-Crash."""
        target_path = self.repo_path / str(payload.get("path", "output.txt"))
        if not target_path.exists():
            return
        result = self._git(
            "status", "--porcelain", str(target_path.relative_to(self.repo_path)),
            check=False,
        )
        if result.returncode == 0 and result.stdout.decode().strip():
            target_path.unlink()
            self._git("checkout", "--", ".", check=False)

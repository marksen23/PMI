"""Auftrag 8 Haltepunkt 2 – rote Tests: CLI-Bedienpfad A1/B1/C1/D1/E2 + G1–G5.

Echte SQLite-DB; Temporal-Client gemockt wo nötig. Kern nicht gemockt.
Kein Auth/Status durch LLM. ProposalOutput-Schema unverändert.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmi.db import get_db, init_schema
from pmi.temporal.activities import (
    CreateRequestInput,
    DraftProposalInput,
    activity_create_request,
    activity_draft_proposal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _auth_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0]
    conn.close()
    return n


def _proposal_row(db_path: Path, proposal_id: str):
    conn = get_db(str(db_path))
    row = conn.execute(
        "SELECT proposed_by, payload_json FROM Proposal WHERE id=?", (proposal_id,)
    ).fetchone()
    conn.close()
    return row


@pytest.fixture
def cli_env(tmp_db_path, tmp_git_repo, monkeypatch):
    monkeypatch.setenv("PMI_DB_PATH", str(tmp_db_path))
    monkeypatch.setenv("PMI_REPO_PATH", str(tmp_git_repo))
    monkeypatch.setenv("PMI_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("PMI_TASK_QUEUE", "pmi-initiation")
    monkeypatch.setenv("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-for-pytest")
    return tmp_db_path, tmp_git_repo


def test_start_creates_workflow_and_request(cli_env, monkeypatch):
    """A1: nach start – Request in DB, workflow_id gesetzt, Proposal agent:llm."""
    from pmi.cli import _cmd_start  # noqa: F401 – fehlt vor Implementierung

    db_path, repo_path = cli_env
    captured: dict = {}

    async def fake_connect(host: str):
        assert host == "localhost:7233"
        client = MagicMock()

        async def start_workflow(fn, arg, *, id, task_queue):
            captured["workflow_id"] = id
            captured["task_queue"] = task_queue
            captured["task"] = arg.task
            draft = await activity_draft_proposal(
                DraftProposalInput(
                    task=arg.task,
                    db_path=str(db_path),
                    step=1,
                    workflow_id=id,
                )
            )
            create = await activity_create_request(
                CreateRequestInput(
                    db_path=str(db_path),
                    repo_path=str(repo_path),
                    proposal_id=draft.proposal_id,
                    step=3,
                    workflow_id=id,
                )
            )
            captured["request_id"] = create.request_id
            captured["proposal_id"] = draft.proposal_id
            return MagicMock()

        client.start_workflow = start_workflow
        return client

    with patch("temporalio.client.Client.connect", side_effect=fake_connect):
        _cmd_start(["Bedienpfad-Testaufgabe"])

    assert captured["task_queue"] == "pmi-initiation"
    conn = get_db(str(db_path))
    req = conn.execute(
        "SELECT id, workflow_id, status FROM ExecutionRequest WHERE id=?",
        (captured["request_id"],),
    ).fetchone()
    conn.close()
    assert req is not None
    assert req["workflow_id"] == captured["workflow_id"]
    assert req["status"] == "OPEN"
    row = _proposal_row(db_path, captured["proposal_id"])
    assert row is not None
    assert row["proposed_by"] == "agent:llm"
    assert "steckbrief" in json.loads(row["payload_json"])


def test_start_prints_ids(cli_env, capsys):
    """A1: stdout enthält request_id, workflow_id, proposal_id."""
    from pmi.cli import _cmd_start

    db_path, repo_path = cli_env
    ids: dict = {}

    async def fake_connect(host: str):
        client = MagicMock()

        async def start_workflow(fn, arg, *, id, task_queue):
            ids["workflow_id"] = id
            draft = await activity_draft_proposal(
                DraftProposalInput(
                    task=arg.task, db_path=str(db_path), step=1, workflow_id=id
                )
            )
            create = await activity_create_request(
                CreateRequestInput(
                    db_path=str(db_path),
                    repo_path=str(repo_path),
                    proposal_id=draft.proposal_id,
                    step=3,
                    workflow_id=id,
                )
            )
            ids["request_id"] = create.request_id
            ids["proposal_id"] = draft.proposal_id
            return MagicMock()

        client.start_workflow = start_workflow
        return client

    with patch("temporalio.client.Client.connect", side_effect=fake_connect):
        _cmd_start(["Print-IDs"])

    out = capsys.readouterr().out
    assert ids["request_id"] in out
    assert ids["workflow_id"] in out
    assert ids["proposal_id"] in out


def test_start_does_not_create_authorization(cli_env):
    """A1: start erzeugt keine Authorization."""
    from pmi.cli import _cmd_start

    db_path, repo_path = cli_env

    async def fake_connect(host: str):
        client = MagicMock()

        async def start_workflow(fn, arg, *, id, task_queue):
            draft = await activity_draft_proposal(
                DraftProposalInput(
                    task=arg.task, db_path=str(db_path), step=1, workflow_id=id
                )
            )
            await activity_create_request(
                CreateRequestInput(
                    db_path=str(db_path),
                    repo_path=str(repo_path),
                    proposal_id=draft.proposal_id,
                    step=3,
                    workflow_id=id,
                )
            )
            return MagicMock()

        client.start_workflow = start_workflow
        return client

    with patch("temporalio.client.Client.connect", side_effect=fake_connect):
        _cmd_start(["Keine-Auth"])

    assert _auth_count(db_path) == 0


def test_show_prints_proposal_json(cli_env, capsys):
    """B1: pmi show druckt gültiges Proposal-JSON."""
    from pmi.cli import _cmd_show
    from pmi.agent_schema import ProposalOutput

    db_path, repo_path = cli_env
    workflow_id = f"show-{uuid.uuid4()}"

    async def _seed():
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Show-Test", db_path=str(db_path), step=1, workflow_id=workflow_id
            )
        )
        create = await activity_create_request(
            CreateRequestInput(
                db_path=str(db_path),
                repo_path=str(repo_path),
                proposal_id=draft.proposal_id,
                step=3,
                workflow_id=workflow_id,
            )
        )
        return create.request_id

    request_id = asyncio.run(_seed())
    _cmd_show([request_id])
    out = capsys.readouterr().out
    data = json.loads(out)
    ProposalOutput.model_validate(data)


def test_status_open_request(cli_env, capsys):
    """C1: OPEN ohne Execution – Request-Status und IDs sichtbar."""
    from pmi.cli import _cmd_status

    db_path, repo_path = cli_env
    workflow_id = f"status-{uuid.uuid4()}"

    async def _seed():
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Status-Test", db_path=str(db_path), step=1, workflow_id=workflow_id
            )
        )
        create = await activity_create_request(
            CreateRequestInput(
                db_path=str(db_path),
                repo_path=str(repo_path),
                proposal_id=draft.proposal_id,
                step=3,
                workflow_id=workflow_id,
            )
        )
        return create.request_id, draft.proposal_id

    request_id, proposal_id = asyncio.run(_seed())
    _cmd_status([request_id])
    out = capsys.readouterr().out
    assert "OPEN" in out
    assert request_id in out
    assert workflow_id in out or proposal_id in out
    assert "APPLIED" not in out


@pytest.mark.asyncio
async def test_worker_uses_env_host_and_queue(monkeypatch):
    """G2/D1: Worker verbindet mit PMI_TEMPORAL_HOST und PMI_TASK_QUEUE."""
    monkeypatch.setenv("PMI_TEMPORAL_HOST", "temporal.example:7233")
    monkeypatch.setenv("PMI_TASK_QUEUE", "pmi-custom-q")

    connect = AsyncMock()
    worker_cm = MagicMock()
    worker_cm.__aenter__ = AsyncMock(return_value=worker_cm)
    worker_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("pmi.temporal.worker.Client.connect", connect):
        with patch("pmi.temporal.worker.Worker", return_value=worker_cm) as worker_cls:
            with patch(
                "pmi.temporal.worker.asyncio.Future",
                side_effect=asyncio.CancelledError,
            ):
                from pmi.temporal import worker as worker_mod

                with pytest.raises(asyncio.CancelledError):
                    await worker_mod.main()

    connect.assert_awaited()
    assert connect.await_args.args[0] == "temporal.example:7233"
    kwargs = worker_cls.call_args.kwargs
    assert kwargs.get("task_queue") == "pmi-custom-q"


def test_eval_justitia_no_authorization(cli_env, capsys):
    """E2: eval justitia schreibt Report; erzeugt keine Authorization."""
    from pmi.cli import _cmd_eval

    db_path, repo_path = cli_env
    workflow_id = f"eval-{uuid.uuid4()}"

    async def _seed():
        draft = await activity_draft_proposal(
            DraftProposalInput(
                task="Eval-Test", db_path=str(db_path), step=1, workflow_id=workflow_id
            )
        )
        create = await activity_create_request(
            CreateRequestInput(
                db_path=str(db_path),
                repo_path=str(repo_path),
                proposal_id=draft.proposal_id,
                step=3,
                workflow_id=workflow_id,
            )
        )
        return create.request_id

    request_id = asyncio.run(_seed())
    before = _auth_count(db_path)
    _cmd_eval(["justitia", request_id])
    out = capsys.readouterr().out
    assert len(out.strip()) > 0
    assert _auth_count(db_path) == before == 0


def test_init_creates_schema(tmp_path, monkeypatch):
    """G4: pmi init legt Schema an PMI_DB_PATH an."""
    from pmi.cli import _cmd_init

    db_path = tmp_path / "init_control.db"
    monkeypatch.setenv("PMI_DB_PATH", str(db_path))
    assert not db_path.exists()
    _cmd_init([])
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "Proposal" in tables
    assert "ExecutionRequest" in tables
    assert "Authorization" in tables


def test_readme_documents_bedienpfad():
    """G1: README nennt die Bedienbefehle."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for cmd in (
        "pmi start",
        "pmi show",
        "pmi approve",
        "pmi run",
        "pmi status",
        "pmi eval",
    ):
        assert cmd in text, f"README fehlt: {cmd}"
    assert "Lokal starten" in text or "lokal starten" in text.lower()


def test_docker_compose_declares_temporal():
    """G3: docker-compose.yml deklariert Temporal (Strukturtest, kein compose up)."""
    candidates = [
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "docker-compose.yaml",
        REPO_ROOT / "compose.yml",
        REPO_ROOT / "compose.yaml",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    assert path is not None, "docker-compose.yml (oder compose.yml) fehlt"
    text = path.read_text(encoding="utf-8").lower()
    assert "temporal" in text


def test_demo_entry_exists():
    """G5: make demo oder scripts/demo.sh vorhanden."""
    makefile = REPO_ROOT / "Makefile"
    script = REPO_ROOT / "scripts" / "demo.sh"
    if makefile.is_file():
        mk = makefile.read_text(encoding="utf-8")
        assert "demo" in mk, "Makefile ohne demo-Ziel"
        return
    assert script.is_file(), "weder Makefile demo-Ziel noch scripts/demo.sh"


def test_task_queue_defaults_provisional():
    """D1: PROVISIONAL Defaults in config."""
    import config

    assert getattr(config, "PMI_TASK_QUEUE", None) == "pmi-initiation" or getattr(
        config, "TEMPORAL_TASK_QUEUE", None
    ) == "pmi-initiation"
    host = getattr(config, "PMI_TEMPORAL_HOST", None) or getattr(
        config, "TEMPORAL_HOST", None
    )
    assert host == "localhost:7233"

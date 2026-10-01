"""Tests für Agent (Steckbrief + Risikoliste) – Phase 1."""
import ast
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from pmi.agent_schema import (
    ProposalOutput,
    Risikoliste,
    Risikoeintrag,
    Steckbrief,
    Stakeholder,
    Meilenstein,
)
from pmi.db import get_db, init_schema, insert_request, append_audit


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _minimal_steckbrief_dict() -> dict:
    return {
        "titel": "Test-Projekt",
        "ausgangslage": "Ist-Zustand XY",
        "ziel": "Soll-Zustand Z",
        "umfang": ["Modul A"],
        "nicht_umfang": [],
        "stakeholder": [{"name": "Max Mustermann", "rolle": "Projektleiter"}],
        "meilensteine": [],
        "budget_rahmen": None,
        "erfolgskriterien": ["Kriterium 1"],
        "annahmen": [],
        "offene_fragen": [],
    }


def _minimal_risiko_dict() -> dict:
    return {
        "beschreibung": "Technisches Risiko",
        "ursache": "Komplexität",
        "auswirkung": "Verzögerung",
        "wahrscheinlichkeit": "mittel",
        "schadensausmass": "mittel",
        "massnahme": "Prototyp bauen",
        "verantwortlich_vorschlag": "Technischer Leiter",
    }


def _minimal_proposal_dict() -> dict:
    return {
        "steckbrief": _minimal_steckbrief_dict(),
        "risikoliste": {"risiken": [_minimal_risiko_dict()]},
    }


def _make_fake_provider(output_dict: dict):
    """Gibt einen FakeLLMProvider zurück, der output_dict als JSON zurückgibt."""
    from pmi.agent import FakeLLMProvider
    return FakeLLMProvider(json.dumps(output_dict))


def _make_db() -> sqlite3.Connection:
    conn = get_db(":memory:")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 1. Valide Ausgabe wird akzeptiert
# ---------------------------------------------------------------------------

def test_valid_output_accepted():
    output = ProposalOutput.model_validate(_minimal_proposal_dict())
    assert output.steckbrief.titel == "Test-Projekt"
    assert len(output.risikoliste.risiken) == 1


# ---------------------------------------------------------------------------
# 2. Ungültiges JSON wird abgelehnt
# ---------------------------------------------------------------------------

def test_invalid_json_rejected():
    from pmi.agent import run_agent, FakeLLMProvider
    conn = _make_db()
    provider = FakeLLMProvider("kein gültiges JSON {")
    with pytest.raises(Exception, match="(?i)(json|invalid|parse)"):
        run_agent(task="Test", conn=conn, provider=provider)


# ---------------------------------------------------------------------------
# 3. Fehlendes Pflichtfeld wird abgelehnt
# ---------------------------------------------------------------------------

def test_missing_field_rejected():
    d = _minimal_proposal_dict()
    del d["steckbrief"]["titel"]
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 4. action/path/scope in LLM-Ausgabe → ValidationError
# ---------------------------------------------------------------------------

def test_action_field_rejected():
    d = _minimal_proposal_dict()
    d["action"] = "write_file"
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


def test_path_field_rejected():
    d = _minimal_proposal_dict()
    d["path"] = "/etc/passwd"
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


def test_scope_field_rejected():
    d = _minimal_proposal_dict()
    d["scope"] = "global"
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 5. Float in Enum-Feld → ValidationError
# ---------------------------------------------------------------------------

def test_float_in_enum_rejected():
    d = _minimal_proposal_dict()
    d["risikoliste"]["risiken"][0]["wahrscheinlichkeit"] = 0.5
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 6. Ungültiger Enum-Wert → ValidationError
# ---------------------------------------------------------------------------

def test_invalid_enum_rejected():
    d = _minimal_proposal_dict()
    d["risikoliste"]["risiken"][0]["wahrscheinlichkeit"] = "sehr_hoch"
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 7. Zu viele Risiken → ValidationError
# ---------------------------------------------------------------------------

def test_too_many_risiken_rejected():
    d = _minimal_proposal_dict()
    d["risikoliste"]["risiken"] = [_minimal_risiko_dict() for _ in range(16)]
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 8. Leere Pflichtlisten → ValidationError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["umfang", "erfolgskriterien"])
def test_empty_required_list_rejected(field):
    d = _minimal_proposal_dict()
    d["steckbrief"][field] = []
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


def test_empty_stakeholder_rejected():
    d = _minimal_proposal_dict()
    d["steckbrief"]["stakeholder"] = []
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


def test_empty_ziel_rejected():
    d = _minimal_proposal_dict()
    d["steckbrief"]["ziel"] = ""
    with pytest.raises(PydanticValidationError):
        ProposalOutput.model_validate(d)


# ---------------------------------------------------------------------------
# 9. Audit-Log wird geschrieben
# ---------------------------------------------------------------------------

def test_audit_log_written():
    from pmi.agent import run_agent, FakeLLMProvider
    conn = _make_db()
    provider = FakeLLMProvider(json.dumps(_minimal_proposal_dict()))
    run_agent(task="Testaufgabe", conn=conn, provider=provider)

    events = conn.execute(
        "SELECT payload_json FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
    ).fetchall()
    assert len(events) >= 1
    payload = json.loads(events[0]["payload_json"])
    for field in ("input_hash", "output_hash", "validated_output_hash", "proposal_input_hash", "model", "version"):
        assert field in payload, f"Fehlendes Audit-Feld: {field}"


# ---------------------------------------------------------------------------
# 10. Audit-Log ist wiederabspielbar (gleiche Eingabe → gleicher output_hash)
# ---------------------------------------------------------------------------

def test_audit_log_replayable():
    from pmi.agent import run_agent, FakeLLMProvider
    conn1 = _make_db()
    conn2 = _make_db()
    provider1 = FakeLLMProvider(json.dumps(_minimal_proposal_dict()))
    provider2 = FakeLLMProvider(json.dumps(_minimal_proposal_dict()))

    run_agent(task="Replizierbare Aufgabe", conn=conn1, provider=provider1)
    run_agent(task="Replizierbare Aufgabe", conn=conn2, provider=provider2)

    hash1 = json.loads(
        conn1.execute(
            "SELECT payload_json FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
        ).fetchone()["payload_json"]
    )["output_hash"]
    hash2 = json.loads(
        conn2.execute(
            "SELECT payload_json FROM AuditEvent WHERE event_type='AGENT_PROPOSAL_GENERATED'"
        ).fetchone()["payload_json"]
    )["output_hash"]
    assert hash1 == hash2


# ---------------------------------------------------------------------------
# 11. Ablagepfad unabhängig von LLM-Ausgabe (auch bei '../' im Titel)
# ---------------------------------------------------------------------------

def test_storage_path_independent_of_llm():
    from pmi.agent_store import store_proposal
    d = _minimal_proposal_dict()
    d["steckbrief"]["titel"] = "../evil"
    output = ProposalOutput.model_validate(d)

    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        stored = store_proposal(output, projekt_id="proj-001", repo_path=repo_path)
        # Path muss unter projekte/proj-001/ liegen, kein traversal
        assert stored.is_relative_to(repo_path / "projekte" / "proj-001")


# ---------------------------------------------------------------------------
# 12. projekt_id mit '../' → ValueError
# ---------------------------------------------------------------------------

def test_path_traversal_rejected():
    from pmi.agent_store import store_proposal
    output = ProposalOutput.model_validate(_minimal_proposal_dict())
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="(?i)(traversal|invalid|path|projekt_id)"):
            store_proposal(output, projekt_id="../evil", repo_path=Path(tmp))


# ---------------------------------------------------------------------------
# 13. Gespeicherter Inhalt entspricht freigegeben ProposalOutput-Hash
# ---------------------------------------------------------------------------

def test_stored_content_matches_approved_hash():
    from pmi.agent_store import store_proposal, compute_stored_hash, compute_expected_hash
    output = ProposalOutput.model_validate(_minimal_proposal_dict())

    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        stored_path = store_proposal(output, projekt_id="proj-hash-test", repo_path=repo_path)
        # Bundle-Hash der gespeicherten Dateien muss mit dem aus dem ProposalOutput übereinstimmen
        stored_hash = compute_stored_hash(stored_path.parent)
        expected_hash = compute_expected_hash(output)
        assert stored_hash == expected_hash


def test_stored_content_mismatch_detected():
    """Nachträgliche Änderung der steckbrief.md → Abweichung erkannt."""
    from pmi.agent_store import store_proposal, compute_stored_hash, compute_expected_hash
    output = ProposalOutput.model_validate(_minimal_proposal_dict())

    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        stored_path = store_proposal(output, projekt_id="proj-mismatch", repo_path=repo_path)
        # Markdown-Datei nachträglich verändern
        steckbrief_file = stored_path.parent / "steckbrief.md"
        steckbrief_file.write_text(steckbrief_file.read_text() + "\nTAMPERED")
        expected_hash = compute_expected_hash(output)
        stored_hash = compute_stored_hash(stored_path.parent)
        assert stored_hash != expected_hash


# ---------------------------------------------------------------------------
# 14. I8: agent.py importiert keine Approval-/Adapter-Funktionen
# ---------------------------------------------------------------------------

def test_i8_no_approve_import():
    src = Path(__file__).parent.parent / "src" / "pmi" / "agent.py"
    tree = ast.parse(src.read_text())
    forbidden = {"approve", "begin_execution", "git_adapter", "GitProjectStoreAdapter"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                assert not any(f in (name or "") for f in forbidden), (
                    f"agent.py importiert verbotene Funktion/Modul: {name}"
                )


# ---------------------------------------------------------------------------
# 15. I8: agent.py enthält kein rohes conn.execute mit INSERT/UPDATE/DELETE
# ---------------------------------------------------------------------------

def test_i8_no_raw_write_execute():
    src = Path(__file__).parent.parent / "src" / "pmi" / "agent.py"
    tree = ast.parse(src.read_text())
    write_keywords = ("INSERT", "UPDATE", "DELETE")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Suche nach .execute(...) mit SQL-String
            if isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
                if node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        upper = first.value.upper()
                        for kw in write_keywords:
                            assert kw not in upper, (
                                f"agent.py enthält rohes execute mit {kw}: {first.value!r}"
                            )


# ---------------------------------------------------------------------------
# Hilfsfunktion für E2E-Tests
# ---------------------------------------------------------------------------

def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo,
    ).decode().strip()


# ---------------------------------------------------------------------------
# 16. Durchstich-Test: run_agent → approve → begin_execution → store_proposal → APPLIED
# ---------------------------------------------------------------------------

def test_e2e_agent_proposal_flow(tmp_git_repo, tmp_db_path):
    """Vollständiger Durchstich für Steckbrief-Proposals durch die PMI-Governance-Kette."""
    from pmi.approval import approve
    from pmi.state_machine import begin_execution, transition, ExecutionStatus
    from pmi.agent import run_agent, FakeLLMProvider
    from pmi.agent_store import store_proposal, compute_stored_hash, compute_expected_hash

    conn = get_db(str(tmp_db_path))
    init_schema(conn)

    # 1. Agent generiert Proposal
    provider = FakeLLMProvider(json.dumps(_minimal_proposal_dict()))
    proposal_id, output = run_agent(task="E2E-Testaufgabe", conn=conn, provider=provider)

    # 2. ExecutionRequest anlegen
    payload = output.model_dump(mode="json")
    head = _git_head(tmp_git_repo)
    request_id = insert_request(
        conn, proposal_id=proposal_id, proposal_version=1,
        payload=payload, expected_revision=head,
    )

    # 3. Genehmigen
    auth = approve(conn, request_id, person="alice")

    # 4. Execution starten
    idem_key = str(uuid.uuid4())
    execution_id = begin_execution(
        conn, request_id=request_id, auth_id=auth.id,
        idempotency_key=idem_key, repo_path=str(tmp_git_repo),
    )
    status = conn.execute(
        "SELECT status FROM Execution WHERE id=?", (execution_id,)
    ).fetchone()["status"]
    assert status == "STARTED"

    # 5. Proposal ablegen (benötigt STARTED Execution)
    stored_path = store_proposal(
        output, projekt_id="proj-e2e-001",
        repo_path=tmp_git_repo, conn=conn, execution_id=execution_id,
    )
    assert stored_path.exists()
    assert compute_stored_hash(stored_path.parent) == compute_expected_hash(output)

    # 6. APPLIED
    transition(conn, execution_id=execution_id, to_state=ExecutionStatus.APPLIED)
    final_status = conn.execute(
        "SELECT status FROM Execution WHERE id=?", (execution_id,)
    ).fetchone()["status"]
    assert final_status == "APPLIED"


# ---------------------------------------------------------------------------
# 17. store_proposal ohne STARTED-Execution → Ablehnung (wie I6)
# ---------------------------------------------------------------------------

def test_store_proposal_requires_started_execution():
    """store_proposal() direkt ohne STARTED-Execution → Ablehnung."""
    from pmi.agent_store import store_proposal
    output = ProposalOutput.model_validate(_minimal_proposal_dict())
    conn = _make_db()

    with tempfile.TemporaryDirectory() as tmp:
        # Nicht-existente execution_id
        with pytest.raises(ValueError, match="(?i)(started|nicht gefunden|status)"):
            store_proposal(
                output, projekt_id="proj-guard", repo_path=Path(tmp),
                conn=conn, execution_id=str(uuid.uuid4()),
            )

        # execution_id=None ohne conn → kein Guard, schreibt normal
        stored = store_proposal(output, projekt_id="proj-noguard", repo_path=Path(tmp))
        assert stored.exists()


# ---------------------------------------------------------------------------
# Hinweis: create_proposal() ist Altlast (aus altem Stub).
# Beim nächsten Testumbau gemeinsam mit Mark entfernen.
# ---------------------------------------------------------------------------

"""Answers / K4: menschliche Antworten → neue Proposal-Version.

Kein Auth, kein Status. decided_by/proposed_by nur mit human:-Präfix (C3).
answers nur vor create_request (D1).
"""
from __future__ import annotations

import copy
import json
import sqlite3


def apply_answers(
    conn: sqlite3.Connection,
    proposal_id: str,
    answers: list[str],
    person: str,
) -> dict:
    """Schreibt Proposal vN+1 mit Diff; erzeugt keine Authorization."""
    if not (person or "").strip():
        raise ValueError("person ist Pflicht (C3)")

    existing_req = conn.execute(
        "SELECT 1 FROM ExecutionRequest WHERE proposal_id=? LIMIT 1",
        (proposal_id,),
    ).fetchone()
    if existing_req is not None:
        raise ValueError("answers nach create_request abgewiesen (D1)")

    latest = conn.execute(
        "SELECT version, payload_json FROM Proposal WHERE id=? "
        "ORDER BY version DESC LIMIT 1",
        (proposal_id,),
    ).fetchone()
    if latest is None:
        raise ValueError(f"unbekanntes proposal_id: {proposal_id}")

    old_version = int(latest["version"])
    old_payload = json.loads(latest["payload_json"])
    new_payload = _merge_answers(old_payload, answers)
    new_version = old_version + 1
    diff = {
        "from_version": old_version,
        "to_version": new_version,
        "answers": list(answers),
        "offene_fragen_vorher": list(
            (old_payload.get("steckbrief") or {}).get("offene_fragen") or []
        ),
    }

    conn.execute(
        "INSERT INTO Proposal (id, version, proposed_by, payload_json, diff_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            proposal_id,
            new_version,
            f"human:{person.strip()}",
            json.dumps(new_payload, ensure_ascii=False),
            json.dumps(diff, ensure_ascii=False),
        ),
    )
    conn.commit()
    return {"proposal_id": proposal_id, "version": new_version}


def _merge_answers(payload: dict, answers: list[str]) -> dict:
    """Antworten in Steckbrief übernehmen; offene_fragen leeren."""
    new_payload = copy.deepcopy(payload)
    steckbrief = new_payload.setdefault("steckbrief", {})
    questions = list(steckbrief.get("offene_fragen") or [])
    steckbrief["offene_fragen"] = []
    annahmen = list(steckbrief.get("annahmen") or [])
    for index, answer in enumerate(answers):
        question = questions[index] if index < len(questions) else f"Antwort {index + 1}"
        annahmen.append(f"{question} → {answer}")
    steckbrief["annahmen"] = annahmen
    return new_payload

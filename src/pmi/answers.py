"""Answers / K4: menschliche Antworten → neue Proposal-Version.

Kein Auth, kein Status. decided_by/proposed_by nur mit human:-Präfix (C3).
answers nur vor create_request (D1).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

from pmi.db import append_audit


def _validate_person(person: str) -> str:
    """Person ohne Namensraum-Präfix; Whitespace/leer abweisen (C3 / Befund 5)."""
    if person is None or not str(person).strip():
        raise ValueError("person ist Pflicht (C3)")
    cleaned = str(person).strip()
    if ":" in cleaned:
        raise ValueError(
            "person darf keinen Namensraum-Präfix enthalten (agent:/human:/…)"
        )
    return cleaned


def apply_answers(
    conn: sqlite3.Connection,
    proposal_id: str,
    answers: list[str],
    person: str,
) -> dict:
    """Schreibt Proposal vN+1 mit Diff; erzeugt keine Authorization.

    BEGIN IMMEDIATE gegen Race mit create_request (D1).
    Idempotent bei gleichem answers-Diff auf der neuesten Version.
    """
    cleaned_person = _validate_person(person)
    answer_list = list(answers)

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing_req = conn.execute(
            "SELECT 1 FROM ExecutionRequest WHERE proposal_id=? LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if existing_req is not None:
            append_audit(
                conn,
                dedup_key=f"answers_rejected:{proposal_id}:{uuid.uuid4()}",
                event_type="ANSWERS_REJECTED",
                execution_id=None,
                seq=0,
                payload={"proposal_id": proposal_id, "reason": "after_create_request"},
                commit=False,
            )
            conn.commit()
            raise ValueError("answers nach create_request abgewiesen (D1)")

        latest = conn.execute(
            "SELECT version, payload_json, COALESCE(diff_json, '') AS diff_json "
            "FROM Proposal WHERE id=? ORDER BY version DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if latest is None:
            conn.execute("ROLLBACK")
            raise ValueError(f"unbekanntes proposal_id: {proposal_id}")

        old_version = int(latest["version"])
        old_payload = json.loads(latest["payload_json"])
        questions = list(
            (old_payload.get("steckbrief") or {}).get("offene_fragen") or []
        )

        # Idempotenz: neueste Version trägt bereits denselben answers-Diff
        diff_raw = latest["diff_json"] or ""
        if diff_raw:
            try:
                existing_diff = json.loads(diff_raw)
            except json.JSONDecodeError:
                existing_diff = {}
            if (
                isinstance(existing_diff, dict)
                and existing_diff.get("answers") == answer_list
            ):
                conn.commit()
                return {"proposal_id": proposal_id, "version": old_version}

        if not questions:
            conn.execute("ROLLBACK")
            raise ValueError("keine offenen Fragen – kein weiteres Versionieren")

        new_payload = _merge_answers(old_payload, answer_list)
        new_version = old_version + 1
        diff = {
            "from_version": old_version,
            "to_version": new_version,
            "answers": answer_list,
            "offene_fragen_vorher": questions,
        }

        conn.execute(
            "INSERT INTO Proposal (id, version, proposed_by, payload_json, diff_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                proposal_id,
                new_version,
                f"human:{cleaned_person}",
                json.dumps(new_payload, ensure_ascii=False),
                json.dumps(diff, ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"proposal_id": proposal_id, "version": new_version}
    except ValueError:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _merge_answers(payload: dict, answers: list[str]) -> dict:
    """Beantwortete Fragen entfernen; unbeantwortete bleiben in offene_fragen."""
    new_payload = copy.deepcopy(payload)
    steckbrief = new_payload.setdefault("steckbrief", {})
    questions = list(steckbrief.get("offene_fragen") or [])
    n = min(len(answers), len(questions))
    steckbrief["offene_fragen"] = questions[n:]
    annahmen = list(steckbrief.get("annahmen") or [])
    for index in range(n):
        annahmen.append(f"{questions[index]} → {answers[index]}")
    # Überschüssige Antworten ohne passende Frage ebenfalls dokumentieren
    for index in range(n, len(answers)):
        annahmen.append(f"Antwort {index + 1} → {answers[index]}")
    steckbrief["annahmen"] = annahmen
    return new_payload

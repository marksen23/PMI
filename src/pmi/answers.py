"""Answers / K4 – Stub für Auftrag 10 Haltepunkt 2.

Absichtlich ohne Versionierung, Diff, Auth-Ablehnung oder D1-Prüfung.
Implementierung erst nach Freigabe HP3.
"""
from __future__ import annotations

import sqlite3


def apply_answers(
    conn: sqlite3.Connection,
    proposal_id: str,
    answers: list[str],
    person: str,
) -> dict:
    """Stub: schreibt keine Proposal-v2, erzeugt keine Authorization, prüft D1 nicht."""
    return {"proposal_id": proposal_id, "version": 1}

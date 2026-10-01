"""Speichert freigegebene ProposalOutput als Markdown unter projekte/<projekt_id>/."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from pmi.agent_schema import ProposalOutput
from pmi.db import _canonical_json


def _validate_projekt_id(projekt_id: str) -> None:
    if not projekt_id or not re.match(r"^[A-Za-z0-9_\-]+$", projekt_id):
        raise ValueError(
            f"Ungültige projekt_id (nur Buchstaben, Ziffern, - und _ erlaubt; kein Pfad-Traversal): {projekt_id!r}"
        )


def _steckbrief_md(output: ProposalOutput) -> str:
    sb = output.steckbrief
    lines = [f"# {sb.titel}", "", f"**Ausgangslage:** {sb.ausgangslage}", "", f"**Ziel:** {sb.ziel}", ""]

    lines += ["## Umfang"]
    for item in sb.umfang:
        lines.append(f"- {item}")
    lines.append("")

    if sb.nicht_umfang:
        lines += ["## Nicht-Umfang"]
        for item in sb.nicht_umfang:
            lines.append(f"- {item}")
        lines.append("")

    lines += ["## Stakeholder"]
    for sh in sb.stakeholder:
        lines.append(f"- **{sh.name}** ({sh.rolle})")
    lines.append("")

    if sb.meilensteine:
        lines += ["## Meilensteine"]
        for m in sb.meilensteine:
            termin = str(m.zieltermin) if m.zieltermin else "offen"
            lines.append(f"- {m.bezeichnung} — {termin}")
        lines.append("")

    if sb.budget_rahmen:
        lines += [f"**Budget-Rahmen:** {sb.budget_rahmen}", ""]

    lines += ["## Erfolgskriterien"]
    for item in sb.erfolgskriterien:
        lines.append(f"- {item}")
    lines.append("")

    if sb.annahmen:
        lines += ["## Annahmen"]
        for item in sb.annahmen:
            lines.append(f"- {item}")
        lines.append("")

    if sb.offene_fragen:
        lines += ["## Offene Fragen"]
        for item in sb.offene_fragen:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def _risikoliste_md(output: ProposalOutput) -> str:
    lines = ["# Risikoliste", ""]
    for i, r in enumerate(output.risikoliste.risiken, 1):
        lines += [
            f"## Risiko {i}: {r.beschreibung}",
            "",
            f"- **Ursache:** {r.ursache}",
            f"- **Auswirkung:** {r.auswirkung}",
            f"- **Wahrscheinlichkeit:** {r.wahrscheinlichkeit}",
            f"- **Schadensausmaß:** {r.schadensausmass}",
            f"- **Maßnahme:** {r.massnahme}",
            f"- **Verantwortlich (Vorschlag):** {r.verantwortlich_vorschlag}",
            "",
        ]
    return "\n".join(lines)


def _bundle_bytes(canonical: bytes, steckbrief_bytes: bytes, risikoliste_bytes: bytes) -> bytes:
    return canonical + b"\x00" + steckbrief_bytes + b"\x00" + risikoliste_bytes


def compute_expected_hash(output: ProposalOutput) -> str:
    """
    Bundle-Hash aus ProposalOutput (ohne Dateisystem-Zugriff).

    Stimmt mit compute_stored_hash() überein, wenn die abgelegten Dateien
    unverändert aus demselben output erzeugt wurden.
    """
    canonical = _canonical_json(output.model_dump(mode="json"))
    sb = _steckbrief_md(output).encode("utf-8")
    rl = _risikoliste_md(output).encode("utf-8")
    return hashlib.sha256(_bundle_bytes(canonical, sb, rl)).hexdigest()


def store_proposal(
    output: ProposalOutput,
    projekt_id: str,
    repo_path: Path,
    conn: sqlite3.Connection | None = None,
    execution_id: str | None = None,
) -> Path:
    """
    Speichert Steckbrief, Risikoliste und proposal.json unter projekte/<projekt_id>/.

    Wenn execution_id angegeben, muss die Execution in STARTED-Status sein (wie I6).
    Der Ablagepfad wird ausschließlich aus projekt_id abgeleitet, nie aus LLM-Ausgabe.
    Returns: Path zur steckbrief.md (Elternverzeichnis = Projektverzeichnis).
    """
    _validate_projekt_id(projekt_id)

    if execution_id is not None:
        if conn is None:
            raise ValueError("conn erforderlich wenn execution_id angegeben")
        row = conn.execute(
            "SELECT status FROM Execution WHERE id=?", (execution_id,)
        ).fetchone()
        status = row["status"] if row else None
        if status != "STARTED":
            raise ValueError(
                f"Execution {execution_id} ist nicht STARTED (status: {status!r})"
            )

    project_dir = repo_path / "projekte" / projekt_id
    project_dir.mkdir(parents=True, exist_ok=True)

    steckbrief_path = project_dir / "steckbrief.md"
    risikoliste_path = project_dir / "risikoliste.md"
    proposal_json_path = project_dir / "proposal.json"

    canonical = _canonical_json(output.model_dump(mode="json"))
    steckbrief_path.write_text(_steckbrief_md(output), encoding="utf-8")
    risikoliste_path.write_text(_risikoliste_md(output), encoding="utf-8")
    proposal_json_path.write_bytes(canonical)

    return steckbrief_path


def compute_stored_hash(project_dir: Path) -> str:
    """
    Bundle-Hash über die drei gespeicherten Dateien (proposal.json + steckbrief.md + risikoliste.md).

    Stimmt mit compute_expected_hash(output) überein, solange keine Datei verändert wurde.
    """
    canonical = (project_dir / "proposal.json").read_bytes()
    sb = (project_dir / "steckbrief.md").read_bytes()
    rl = (project_dir / "risikoliste.md").read_bytes()
    return hashlib.sha256(_bundle_bytes(canonical, sb, rl)).hexdigest()

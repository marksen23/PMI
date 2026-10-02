"""Bewertet ein ProposalOutput gegen den Vergabebescheid TED 274205-2025.

Kein Modell, kein Netz. Die Kriterien folgen L8.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pmi.agent_schema import ProposalOutput
from pmi.ihp_reference import (
    END_DATE,
    HOECHSTES_ANGEBOT_EUR,
    START_DATE,
    ZUSCHLAG_EUR,
)

_ZIFFER = re.compile(r"\d")
_FLOSKEL = re.compile(r"projektleitung|regelmä(?:ss|ß)ig prüfen", re.IGNORECASE)


def _normalize_amount(text: str) -> float | None:
    """Wandelt deutschen Betrag in float um.

    Akzeptiert „317.829,00" (Punkt = Tausender, Komma = Dezimal)
    sowie „317829.00" (englisches Format).
    Gibt None zurück, wenn kein Betrag erkennbar ist.
    """
    if text is None:
        return None
    # Nicht-numerische Suffixe (z.B. " EUR") entfernen
    t = re.sub(r"[^\d.,]", " ", str(text)).strip()
    # Führende/abschließende Leerzeichen zwischen Ziffern kompaktieren
    t = t.strip()
    if not t:
        return None
    # Deutsches Format erkennen: letzte Trenner bestimmen Format
    # Wenn Komma vorkommt: Punkt = Tausender, Komma = Dezimal
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    # Leerzeichen (als Tausendertrenner) entfernen
    t = t.replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None


@dataclass(frozen=True)
class IhpEval:
    hart_pass: bool
    hart_details: tuple[str, ...]
    schwach: tuple[str, ...]


def evaluate(output: ProposalOutput) -> IhpEval:
    """Bewertet output nach den harten und schwachen Kriterien aus L8."""
    details: list[str] = []
    hart_pass = True

    # --- Hartes Kriterium 1: budget_rahmen muss 317829.00 EUR sein ---
    br = output.steckbrief.budget_rahmen
    if br is None:
        hart_pass = False
        details.append("budget_rahmen ist leer (erwartet: 317829.00 EUR)")
    else:
        amount = _normalize_amount(br)
        if amount is None or abs(amount - ZUSCHLAG_EUR) > 0.01:
            hart_pass = False
            details.append(
                f"budget_rahmen ist {br!r}, erwartet {ZUSCHLAG_EUR} EUR"
            )

    # --- Hartes Kriterium 2: meilensteine darf nicht leer sein ---
    if len(output.steckbrief.meilensteine) == 0:
        hart_pass = False
        details.append("meilensteine ist leer (Laufzeitdaten fehlen)")

    # --- Hartes Kriterium 3+4: beide Laufzeitdaten müssen irgendwo vorkommen ---
    payload = output.model_dump_json()
    if START_DATE not in payload:
        hart_pass = False
        details.append(f"StartDate {START_DATE} nicht im Proposal")
    if END_DATE not in payload:
        hart_pass = False
        details.append(f"EndDate {END_DATE} nicht im Proposal")

    # --- Schwach: erfundene Zahlen, Daten oder Rollen in den Risikofeldern ---
    schwach: list[str] = []
    _CHECK_FELDER = ("ursache", "auswirkung", "massnahme", "verantwortlich_vorschlag")
    for i, risiko in enumerate(output.risikoliste.risiken):
        for feld in _CHECK_FELDER:
            val = getattr(risiko, feld, "") or ""
            if _ZIFFER.search(val) or _FLOSKEL.search(val):
                schwach.append(f"risiken[{i}].{feld}")

    return IhpEval(
        hart_pass=hart_pass,
        hart_details=tuple(details),
        schwach=tuple(schwach),
    )

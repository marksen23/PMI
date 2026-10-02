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
_HOECHST = re.compile(r"h[öo]chst", re.IGNORECASE)
# Deutsche Beträge, EN mit Cent, oder längere Ganzzahlen (Zuschlag ohne Trenner).
_AMOUNT_TOKEN = re.compile(
    r"\d{1,3}(?:\.\d{3})+,\d{2}"
    r"|\d{1,3}(?:\s\d{3})+,\d{2}"
    r"|\d+,\d{2}"
    r"|\d+\.\d{2}"
    r"|\d{5,}"
)


def _normalize_amount(text: str | None) -> float | None:
    """Wandelt einen einzelnen Betrag in float um.

    Akzeptiert „317.829,00" (Punkt = Tausender, Komma = Dezimal)
    sowie „317829.00" (englisches Format).
    """
    if text is None:
        return None
    t = re.sub(r"[^\d.,]", "", str(text)).strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _amounts_in(text: str) -> tuple[float, ...]:
    found: list[float] = []
    for match in _AMOUNT_TOKEN.finditer(text):
        value = _normalize_amount(match.group(0))
        if value is not None:
            found.append(value)
    return tuple(found)


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= 0.01


def _zieltermin_str(zieltermin: object) -> str | None:
    if zieltermin is None:
        return None
    if hasattr(zieltermin, "isoformat"):
        return str(zieltermin.isoformat())
    text = str(zieltermin).strip()
    return text or None


@dataclass(frozen=True)
class IhpEval:
    hart_pass: bool
    hart_details: tuple[str, ...]
    unbelegt: tuple[str, ...]
    schwach: tuple[str, ...]


def evaluate(output: ProposalOutput) -> IhpEval:
    """Bewertet output nach den harten, unbelegten und schwachen Kriterien aus L8."""
    details: list[str] = []
    hart_pass = True

    br = output.steckbrief.budget_rahmen
    if br is None or not str(br).strip():
        hart_pass = False
        details.append("budget_rahmen ist leer (erwartet: 317829.00 EUR)")
    else:
        amounts = _amounts_in(str(br))
        has_award = any(_near(a, ZUSCHLAG_EUR) for a in amounts)
        has_higher = any(_near(a, HOECHSTES_ANGEBOT_EUR) for a in amounts)
        if not has_award:
            hart_pass = False
            details.append(
                f"budget_rahmen ist {br!r}, erwartet Zuschlag {ZUSCHLAG_EUR} EUR"
            )
        elif has_higher and not _HOECHST.search(str(br)):
            hart_pass = False
            details.append(
                "858074.65 EUR in budget_rahmen ohne Kennzeichnung als höchstes Angebot"
            )

    if len(output.steckbrief.meilensteine) == 0:
        hart_pass = False
        details.append("meilensteine ist leer (Laufzeitdaten fehlen)")

    payload = output.model_dump_json()
    if START_DATE not in payload:
        hart_pass = False
        details.append(f"StartDate {START_DATE} nicht im Proposal")
    if END_DATE not in payload:
        hart_pass = False
        details.append(f"EndDate {END_DATE} nicht im Proposal")

    unbelegt: list[str] = []
    for index, meilenstein in enumerate(output.steckbrief.meilensteine):
        termin = _zieltermin_str(meilenstein.zieltermin)
        if termin is None:
            continue
        if START_DATE in termin or END_DATE in termin:
            continue
        unbelegt.append(f"meilensteine[{index}].zieltermin={termin}")

    schwach: list[str] = []
    check_felder = ("ursache", "auswirkung", "massnahme", "verantwortlich_vorschlag")
    for index, risiko in enumerate(output.risikoliste.risiken):
        for feld in check_felder:
            val = getattr(risiko, feld, "") or ""
            if _ZIFFER.search(val) or _FLOSKEL.search(val):
                schwach.append(f"risiken[{index}].{feld}")

    return IhpEval(
        hart_pass=hart_pass,
        hart_details=tuple(details),
        unbelegt=tuple(unbelegt),
        schwach=tuple(schwach),
    )

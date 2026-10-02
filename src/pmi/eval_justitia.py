"""Bewertet ein ProposalOutput gegen die Kurzfassung Justitia 4.0.

Kein Modell, kein Netz. Die drei Noten folgen L6.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pmi.agent_schema import ProposalOutput
from pmi.justitia_reference import KAPITEL7_SAETZE

_FLOSKELN = (
    "regelmässig prüfen",
    "regelmäßig prüfen",
    "projektleitung",
)
_ERGAENZT_FELDER = ("wahrscheinlichkeit", "schadensausmass")
_TEXTFELDER = ("ursache", "auswirkung", "massnahme", "verantwortlich_vorschlag")
_ZIFFER = re.compile(r"\d")


def whitespace_collapse(text: str) -> str:
    return " ".join(text.split())


def _norm(text: str) -> str:
    return whitespace_collapse(text).lower()


def zuordnung(beschreibung: str) -> tuple[int, ...]:
    """Indizes der Sätze, die die Beschreibung trifft. Leer trifft keinen Satz."""
    left = _norm(beschreibung)
    if not left:
        return ()
    hits: list[int] = []
    for index, satz in enumerate(KAPITEL7_SAETZE):
        right = _norm(satz)
        if not right:
            continue
        if right in left or left in right:
            hits.append(index)
    return tuple(hits)


@dataclass(frozen=True)
class JustitiaEval:
    hart_pass: bool
    unbelegte_indizes: tuple[int, ...]
    treffer: tuple[tuple[int, ...], ...]
    fehlende_saetze: tuple[int, ...]
    doppelte_saetze: tuple[int, ...]
    schwach: tuple[str, ...]
    ergaenzt: tuple[str, ...]


def evaluate(output: ProposalOutput) -> JustitiaEval:
    hart_pass = (
        len(output.steckbrief.meilensteine) == 0
        and output.steckbrief.budget_rahmen is None
    )
    treffer: list[tuple[int, ...]] = []
    unbelegt: list[int] = []
    belegt = [0] * len(KAPITEL7_SAETZE)
    schwach: list[str] = []
    ergaenzt: list[str] = []
    for index, risiko in enumerate(output.risikoliste.risiken):
        hits = zuordnung(risiko.beschreibung)
        treffer.append(hits)
        if not hits:
            unbelegt.append(index)
        for hit in hits:
            belegt[hit] += 1
        for feld in _ERGAENZT_FELDER:
            ergaenzt.append(f"risiken[{index}].{feld}")
        for feld in _TEXTFELDER:
            text = _norm(getattr(risiko, feld))
            if _ZIFFER.search(text) or any(floskel in text for floskel in _FLOSKELN):
                schwach.append(f"risiken[{index}].{feld}")
    fehlend = tuple(i for i, anzahl in enumerate(belegt) if anzahl == 0)
    doppelt = tuple(i for i, anzahl in enumerate(belegt) if anzahl > 1)
    return JustitiaEval(
        hart_pass=hart_pass,
        unbelegte_indizes=tuple(unbelegt),
        treffer=tuple(treffer),
        fehlende_saetze=fehlend,
        doppelte_saetze=doppelt,
        schwach=tuple(schwach),
        ergaenzt=tuple(ergaenzt),
    )

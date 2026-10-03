"""Adapter ProposalOutput → interne Sicht fakten/aussagen (nur Evaluator).

Haltepunkt 2: Stub. Implementierung erst nach Freigabe der Tests.
"""
from __future__ import annotations

from dataclasses import dataclass

from pmi.agent_schema import ProposalOutput


@dataclass(frozen=True)
class Fakt:
    key: str
    wert: str
    zitat: str | None
    herkunft: str


@dataclass(frozen=True)
class Aussage:
    text: str
    herkunft: str


@dataclass(frozen=True)
class AdapterView:
    fakten: tuple[Fakt, ...]
    aussagen: tuple[Aussage, ...]
    konflikte: tuple[str, ...]  # keys with conflicting values


def adapt(output: ProposalOutput) -> AdapterView:
    raise NotImplementedError("nd_bed_management_adapter.adapt – Haltepunkt 2")

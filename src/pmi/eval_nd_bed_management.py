"""Bewertet ProposalOutput gegen ND HHS Bed Management Closeout.

Kein Modell, kein Netz. Haltepunkt 2: Stub. Implementierung nach Freigabe.
"""
from __future__ import annotations

from dataclasses import dataclass

from pmi.agent_schema import ProposalOutput


@dataclass(frozen=True)
class NdBedEval:
    hart_pass: bool
    unbelegt: tuple[str, ...]
    schwach: tuple[str, ...]


def evaluate(output: ProposalOutput) -> NdBedEval:
    raise NotImplementedError("eval_nd_bed_management.evaluate – Haltepunkt 2")

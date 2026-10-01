"""Pydantic-Schemas für Agent-Ausgabe (Steckbrief + Risikoliste)."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Stakeholder(BaseModel, extra="forbid"):
    name: str
    rolle: str


class Meilenstein(BaseModel, extra="forbid"):
    bezeichnung: str
    zieltermin: date | None


class Steckbrief(BaseModel, extra="forbid"):
    titel: str
    ausgangslage: str
    ziel: Annotated[str, Field(min_length=1)]
    umfang: Annotated[list[str], Field(min_length=1)]
    nicht_umfang: list[str]
    stakeholder: Annotated[list[Stakeholder], Field(min_length=1)]
    meilensteine: list[Meilenstein]
    budget_rahmen: str | None
    erfolgskriterien: Annotated[list[str], Field(min_length=1)]
    annahmen: list[str]
    offene_fragen: list[str]


class Risikoeintrag(BaseModel, extra="forbid"):
    beschreibung: str
    ursache: str
    auswirkung: str
    wahrscheinlichkeit: Literal["niedrig", "mittel", "hoch"]
    schadensausmass: Literal["niedrig", "mittel", "hoch"]
    massnahme: str
    verantwortlich_vorschlag: str


class Risikoliste(BaseModel, extra="forbid"):
    risiken: Annotated[list[Risikoeintrag], Field(min_length=1, max_length=15)]


class ProposalOutput(BaseModel, extra="forbid"):
    steckbrief: Steckbrief
    risikoliste: Risikoliste

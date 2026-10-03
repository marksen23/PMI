"""Adapter ProposalOutput → interne Sicht fakten/aussagen (nur Evaluator).

Schema bleibt unverändert. KEY-Zeilen nur in Freitextfeldern.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pmi.agent_schema import ProposalOutput
from pmi.nd_bed_management_reference import REFERENCE_KEYS

_KEY_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*:\s*(.*)$")
_ZITAT_SUFFIX = "_ZITAT"


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
    konflikte: tuple[str, ...]


def _norm_conflict(wert: str) -> str:
    """Vergleichsnormalisierung für KEY-Konflikte über Felder hinweg."""
    return " ".join(wert.split()).casefold()


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _iter_source_blobs(output: ProposalOutput) -> list[tuple[str, str]]:
    """Liste (herkunft, text) aus den freigegebenen Quellfeldern."""
    sb = output.steckbrief
    blobs: list[tuple[str, str]] = []
    if sb.budget_rahmen:
        blobs.append(("steckbrief.budget_rahmen", sb.budget_rahmen))
    blobs.append(("steckbrief.ausgangslage", sb.ausgangslage))
    for i, kriterium in enumerate(sb.erfolgskriterien):
        blobs.append((f"steckbrief.erfolgskriterien[{i}]", kriterium))
    for i, annahme in enumerate(sb.annahmen):
        blobs.append((f"steckbrief.annahmen[{i}]", annahme))
    for i, meilenstein in enumerate(sb.meilensteine):
        blobs.append((f"steckbrief.meilensteine[{i}]", meilenstein.bezeichnung))
        if meilenstein.zieltermin is not None:
            blobs.append(
                (
                    f"steckbrief.meilensteine[{i}].zieltermin",
                    meilenstein.zieltermin.isoformat(),
                )
            )
    for i, risiko in enumerate(output.risikoliste.risiken):
        blobs.append((f"risikoliste[{i}].beschreibung", risiko.beschreibung))
    return blobs


def adapt(output: ProposalOutput) -> AdapterView:
    raw: dict[str, list[tuple[str, str, str | None]]] = {}
    # key → list of (wert, herkunft, zitat)
    aussagen: list[Aussage] = []

    for herkunft, text in _iter_source_blobs(output):
        lines = text.splitlines() if "\n" in text else [text]
        # Also allow single-line blobs without newlines
        if len(lines) == 1 and "\n" not in text:
            lines = text.splitlines() or [text]

        i = 0
        non_key_chunks: list[str] = []
        while i < len(lines):
            line = lines[i]
            match = _KEY_LINE.match(line)
            if match:
                name = match.group(1)
                if name.endswith(_ZITAT_SUFFIX):
                    base = name[: -len(_ZITAT_SUFFIX)]
                    if base in REFERENCE_KEYS:
                        zitat_val = match.group(2)
                        if base not in raw:
                            raw[base] = []
                        # attach zitat to last fakt of this key from same herkunft if possible
                        attached = False
                        for idx in range(len(raw[base]) - 1, -1, -1):
                            wert, her, zit = raw[base][idx]
                            if her == herkunft and zit is None:
                                raw[base][idx] = (wert, her, zitat_val)
                                attached = True
                                break
                        if not attached:
                            raw.setdefault(base, []).append(("", herkunft, zitat_val))
                        i += 1
                        continue
                if name in REFERENCE_KEYS:
                    wert = match.group(2)
                    zitat: str | None = None
                    if i + 1 < len(lines):
                        nxt = _KEY_LINE.match(lines[i + 1])
                        if (
                            nxt
                            and nxt.group(1) == f"{name}{_ZITAT_SUFFIX}"
                        ):
                            zitat = nxt.group(2)
                            i += 1
                    raw.setdefault(name, []).append((wert, herkunft, zitat))
                    i += 1
                    continue
            non_key_chunks.append(line)
            i += 1

        leftover = "\n".join(non_key_chunks).strip()
        if leftover:
            # Herkunftsindex für Aussagen: Feld[n] bei mehrzeiligen/satzweisen Stücken
            sentences = _split_sentences(leftover)
            # For single-field blobs without sentence punctuation, one aussage
            if not sentences:
                sentences = [leftover]
            base_path = herkunft
            # risikoliste[0].beschreibung / ausgangslage → [n] suffix
            for n, satz in enumerate(sentences):
                if base_path.startswith("steckbrief.ausgangslage") or ".beschreibung" in base_path:
                    path = f"{base_path}[{n}]"
                elif re.search(r"\[\d+\]$", base_path):
                    path = f"{base_path}" if len(sentences) == 1 else f"{base_path}[{n}]"
                else:
                    path = base_path if len(sentences) == 1 else f"{base_path}[{n}]"
                aussagen.append(Aussage(text=satz, herkunft=path))

    fakten: list[Fakt] = []
    konflikte: list[str] = []
    for key, entries in raw.items():
        norms = {_norm_conflict(w) for w, _, _ in entries if w is not None}
        # empty string is a value
        norms = {_norm_conflict(w) for w, _, _ in entries}
        if len(norms) > 1:
            konflikte.append(key)
        for wert, herkunft, zitat in entries:
            fakten.append(Fakt(key=key, wert=wert, zitat=zitat, herkunft=herkunft))

    return AdapterView(
        fakten=tuple(fakten),
        aussagen=tuple(aussagen),
        konflikte=tuple(sorted(set(konflikte))),
    )

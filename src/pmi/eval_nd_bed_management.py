"""Bewertet ProposalOutput gegen ND HHS Bed Management Closeout.

Kein Modell, kein Netz. Hart / Unbelegt / Schwach nach freigegebenem Raster.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from pmi.agent_schema import ProposalOutput
from pmi.nd_bed_management_adapter import AdapterView, adapt
from pmi.nd_bed_management_reference import (
    ACTUAL_COST,
    ACTUAL_FINISH_DATE,
    FINAL_BASELINE_BUDGET,
    FINAL_BASELINE_END_DATE,
    FINAL_COST_VARIANCE_DIR,
    FINAL_COST_VARIANCE_PCT,
    FINAL_SCHEDULE_VARIANCE,
    FUNDING_FEDERAL_SHARE,
    FUNDING_STATE_SHARE,
    MAJOR_SCOPE_CHANGES_OK,
    OBJECTIVE_STATUS_FAIL,
    OBJECTIVE_STATUS_OK,
    ORIGINAL_BASELINE_BUDGET,
    ORIGINAL_BASELINE_END_DATE,
    ORIGINAL_COST_VARIANCE_DIR,
    ORIGINAL_COST_VARIANCE_PCT,
    ORIGINAL_SCHEDULE_VARIANCE,
    PROJECT_START_DATE,
    SOURCE_SENTENCES,
    VENDOR,
)

_PLACEHOLDER = frozenset({"", "tbd", "tba", "n/a", "?", "xxx", "none"})
_DE_EXACT = re.compile(
    r"^[\$€]?\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*(?:\$|€|usd|eur)?$",
    re.IGNORECASE,
)
_DE_AMBIG = re.compile(r"^\d{1,3}(?:\.\d{3})+$")  # 490.404 ohne Nachkomma
_US_MONEY = re.compile(
    r"^[\$€]?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[kmb]?$",
    re.IGNORECASE,
)
_ABBREV = re.compile(
    r"^[\$€]?\s*\d+(?:[.,]\d+)?\s*[kmb]$",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*%")
_DIR_OVER = frozenset({"over", "über", "überschreitung", "+"})
_DIR_UNDER = frozenset({"under", "unter", "unterschreitung", "-"})
_HAS_DIGIT = re.compile(r"\d")
_AMOUNT_LIKE = re.compile(
    r"\$\s*\d|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+[.,]\d{2}|\d+\s*%|\d{1,2}/\d{1,2}/\d{2,4}"
)


@dataclass(frozen=True)
class NdBedEval:
    hart_pass: bool
    unbelegt: tuple[str, ...]
    schwach: tuple[str, ...]


def _ws_lower(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(text.split()).casefold()


def _is_placeholder(wert: str) -> bool:
    return _ws_lower(wert).strip() in _PLACEHOLDER


def _strip_currency(text: str) -> str:
    return re.sub(r"[\$€]|usd|eur", "", text, flags=re.IGNORECASE).strip()


def _parse_amount(text: str) -> tuple[float | None, str]:
    """Returns (amount_or_None, kind) kind in exact|abbrev|ambiguous|invalid."""
    raw = text.strip()
    if not raw or _is_placeholder(raw):
        return None, "invalid"
    cleaned = _strip_currency(raw)
    cleaned = cleaned.replace(" ", "")
    if _DE_EXACT.match(raw.strip()) or _DE_EXACT.match(
        re.sub(r"\s+", " ", raw.strip())
    ):
        # 627.678,14
        t = _strip_currency(raw).replace(" ", "")
        t = t.replace(".", "").replace(",", ".")
        try:
            return float(t), "exact"
        except ValueError:
            return None, "invalid"
    if _DE_AMBIG.match(cleaned):
        # 490.404 – mehrdeutig
        t = cleaned.replace(".", "")
        try:
            return float(t), "ambiguous"
        except ValueError:
            return None, "invalid"
    # Abbrev $0.58M / $490K
    m = re.fullmatch(
        r"\$?\s*(\d+(?:\.\d+)?)\s*([kmb])",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    if m:
        base = float(m.group(1))
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
        return base * mult, "abbrev"
    # US: 577678.14 / 490,404 / 490,404.00
    t = cleaned.replace(",", "")
    try:
        return float(t), "exact"
    except ValueError:
        return None, "invalid"


def _amount_matches(expected: float, got: float, kind: str) -> tuple[bool, bool]:
    """Returns (hart_ok, schwach)."""
    if kind == "abbrev":
        # ±½ letzte Stelle der Abbrev-Skala:
        # $0.58M → 0.01M = 10000, Hälfte 5000; $490K → 1K, Hälfte 500
        diff = abs(got - expected)
        scale = max(got, expected)
        if scale >= 100_000:  # Millionen-Abbrev (auch 0.58M)
            step = 5_000
        elif scale >= 1_000:
            step = 500
        else:
            step = 0.5
        if diff <= step:
            return True, True
        return False, False
    if kind == "ambiguous":
        if abs(got - expected) < 0.01:
            return True, True
        return False, False
    if abs(got - expected) < 0.01:
        return True, False
    return False, False


def _parse_date(text: str) -> date | None:
    text = text.strip()
    if not text or _is_placeholder(text):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Partial dates like "April 2026" / "2026" → not full parse
    return None


def _parse_percent(text: str) -> float | None:
    m = _PERCENT.search(text)
    if not m:
        # bare integer?
        try:
            return float(text.strip().replace(",", "."))
        except ValueError:
            return None
    return float(m.group(1).replace(",", "."))


def _parse_cost_variance(text: str) -> tuple[float | None, str | None]:
    """Returns (percent, direction) direction in over|under|None."""
    pct = _parse_percent(text)
    low = _ws_lower(text)
    direction: str | None = None
    for token in _DIR_OVER:
        if token in low:
            direction = "over"
            break
    if direction is None:
        for token in _DIR_UNDER:
            if token in low:
                direction = "under"
                break
    # leading +/- 
    if direction is None:
        if re.search(r"(^|[\s])\+\s*\d", text):
            direction = "over"
        elif re.search(r"(^|[\s])-\s*\d", text):
            direction = "under"
    return pct, direction


def _zuordnung(text: str) -> bool:
    left = _ws_lower(text)
    if not left:
        return False
    for satz in SOURCE_SENTENCES:
        right = _ws_lower(satz)
        if not right:
            continue
        if right in left or left in right:
            return True
    return False


def _ref_amount_set() -> set[float]:
    return {
        float(ORIGINAL_BASELINE_BUDGET),
        float(FINAL_BASELINE_BUDGET),
        float(ACTUAL_COST),
        float(FUNDING_STATE_SHARE),
        float(FUNDING_FEDERAL_SHARE),
        float(ORIGINAL_SCHEDULE_VARIANCE),
        float(FINAL_SCHEDULE_VARIANCE),
        float(ORIGINAL_COST_VARIANCE_PCT),
        float(FINAL_COST_VARIANCE_PCT),
        99.0,  # mentioned in OBJECTIVE_3 outcome
        95.0,
    }


def _aussage_unbelegt(text: str) -> bool:
    if _zuordnung(text):
        # still unbelegt if contains foreign quantified claim? T15 matches → not unbelegt
        return False
    # no treffer
    if _AMOUNT_LIKE.search(text) or _HAS_DIGIT.search(text):
        # digit present and not matched → unbelegt (T20, T17, T24, T13)
        return True
    # no treffer without digits: ignore fillers (T18)
    return False


_BUDGET_KEYS = {
    "ORIGINAL_BASELINE_BUDGET": float(ORIGINAL_BASELINE_BUDGET),
    "FINAL_BASELINE_BUDGET": float(FINAL_BASELINE_BUDGET),
    "ACTUAL_COST": float(ACTUAL_COST),
}
_DATE_KEYS = {
    "PROJECT_START_DATE": date.fromisoformat(PROJECT_START_DATE),
    "ORIGINAL_BASELINE_END_DATE": date.fromisoformat(ORIGINAL_BASELINE_END_DATE),
    "FINAL_BASELINE_END_DATE": date.fromisoformat(FINAL_BASELINE_END_DATE),
    "ACTUAL_FINISH_DATE": date.fromisoformat(ACTUAL_FINISH_DATE),
}
_SCHED_KEYS = {
    "ORIGINAL_SCHEDULE_VARIANCE": ORIGINAL_SCHEDULE_VARIANCE,
    "FINAL_SCHEDULE_VARIANCE": FINAL_SCHEDULE_VARIANCE,
}
_COST_VAR_KEYS = {
    "ORIGINAL_COST_VARIANCE": (ORIGINAL_COST_VARIANCE_PCT, ORIGINAL_COST_VARIANCE_DIR),
    "FINAL_COST_VARIANCE": (FINAL_COST_VARIANCE_PCT, FINAL_COST_VARIANCE_DIR),
}
_FUNDING_KEYS = {
    "FUNDING_STATE_SHARE": FUNDING_STATE_SHARE,
    "FUNDING_FEDERAL_SHARE": FUNDING_FEDERAL_SHARE,
}
_OBJ_STATUS_KEYS = frozenset(
    {"OBJECTIVE_1_STATUS", "OBJECTIVE_2_STATUS", "OBJECTIVE_3_STATUS"}
)
_OBJ_SCHWACH_ZITAT = frozenset({"OBJECTIVE_1_STATUS", "OBJECTIVE_3_STATUS"})


def evaluate(output: ProposalOutput) -> NdBedEval:
    view: AdapterView = adapt(output)
    hart_ok = True
    unbelegt: list[str] = []
    schwach: list[str] = []

    if view.konflikte:
        hart_ok = False

    # Evaluate each distinct key once (use first fakt for value checks;
    # herkunft paths collected for reporting)
    by_key: dict[str, list] = {}
    for fakt in view.fakten:
        by_key.setdefault(fakt.key, []).append(fakt)

    for key, fakts in by_key.items():
        if key in view.konflikte:
            continue  # already hart fail
        fakt = fakts[0]
        herkunft = fakt.herkunft
        wert = fakt.wert

        if key == "FINAL_BASELINE_START_DATE":
            if wert.strip():
                unbelegt.append(herkunft)
            continue

        if key in _BUDGET_KEYS:
            if _is_placeholder(wert) or not wert.strip():
                hart_ok = False
                continue
            amount, kind = _parse_amount(wert)
            if amount is None:
                hart_ok = False
                continue
            ok, weak = _amount_matches(_BUDGET_KEYS[key], amount, kind)
            if not ok:
                hart_ok = False
            elif weak:
                schwach.append(herkunft)
            continue

        if key in _DATE_KEYS:
            parsed = _parse_date(wert)
            if parsed is None:
                # partial date → schwach if year/month hints match? Raster: Teildatum = SCHWACH
                if re.search(r"\d{4}|january|february|march|april|may|june|july|august|september|october|november|december", wert, re.I):
                    # if clearly wrong full date failed; partial that matches year of expected
                    expected = _DATE_KEYS[key]
                    if str(expected.year) in wert or expected.strftime("%B").lower() in wert.casefold():
                        schwach.append(herkunft)
                    else:
                        hart_ok = False
                else:
                    hart_ok = False
                continue
            if parsed != _DATE_KEYS[key]:
                hart_ok = False
            continue

        if key in _SCHED_KEYS:
            if _is_placeholder(wert):
                hart_ok = False
                continue
            pct = _parse_percent(wert)
            if pct is None:
                hart_ok = False
                continue
            expected = float(_SCHED_KEYS[key])
            if abs(round(pct) - expected) > 0:
                if abs(pct - expected) < 0.01:
                    pass
                elif abs(round(pct) - expected) == 0:
                    schwach.append(herkunft)  # 52.4 → 52
                else:
                    hart_ok = False
                    continue
            else:
                if abs(pct - expected) > 0.01:
                    schwach.append(herkunft)
            continue

        if key in _COST_VAR_KEYS:
            if _is_placeholder(wert):
                hart_ok = False
                continue
            exp_pct, exp_dir = _COST_VAR_KEYS[key]
            pct, direction = _parse_cost_variance(wert)
            if pct is None:
                hart_ok = False
                continue
            if abs(round(pct) - exp_pct) > 0 and abs(pct - exp_pct) > 0.01:
                hart_ok = False
                continue
            if direction is None:
                # richtiges Prozent ohne Richtung → Pass + schwach (T09)
                if abs(round(pct) - exp_pct) == 0 or abs(pct - exp_pct) < 0.5:
                    schwach.append(herkunft)
                else:
                    hart_ok = False
                continue
            if direction != exp_dir:
                hart_ok = False
                continue
            if abs(pct - exp_pct) > 0.01 and abs(round(pct) - exp_pct) == 0:
                schwach.append(herkunft)
            continue

        if key == "VENDOR":
            if _is_placeholder(wert) or _ws_lower(wert) != _ws_lower(VENDOR):
                hart_ok = False
            continue

        if key == "MAJOR_SCOPE_CHANGES":
            if _ws_lower(wert) not in MAJOR_SCOPE_CHANGES_OK:
                hart_ok = False
            continue

        if key in _OBJ_STATUS_KEYS:
            low = _ws_lower(wert)
            if low in OBJECTIVE_STATUS_FAIL or (
                low not in OBJECTIVE_STATUS_OK and not _is_placeholder(wert)
            ):
                if low in OBJECTIVE_STATUS_FAIL or low not in OBJECTIVE_STATUS_OK:
                    hart_ok = False
                    continue
            if low not in OBJECTIVE_STATUS_OK:
                hart_ok = False
                continue
            # Met but empty zitat → schwach for OBJ 1 and 3
            if key in _OBJ_SCHWACH_ZITAT:
                zitat = fakt.zitat
                if zitat is None or not str(zitat).strip():
                    schwach.append(herkunft)
            continue

        if key in _FUNDING_KEYS:
            pct = _parse_percent(wert)
            if pct is None or abs(pct - _FUNDING_KEYS[key]) > 0.01:
                hart_ok = False
            continue

        # Other keys present: not in HART set → ignore for hart (B: missing not fail;
        # extra keys like OBJECTIVE_1 text not required)

    for aussage in view.aussagen:
        if _aussage_unbelegt(aussage.text):
            unbelegt.append(aussage.herkunft)

    # dedupe preserve order
    def _uniq(items: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return tuple(out)

    return NdBedEval(
        hart_pass=hart_ok,
        unbelegt=_uniq(unbelegt),
        schwach=_uniq(schwach),
    )

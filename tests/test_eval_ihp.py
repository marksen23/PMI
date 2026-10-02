"""Prüfer für den Vergabebescheid TED 274205-2025 (IHP DMS). Kein Netz, kein Modell."""
from __future__ import annotations

import pytest

from pmi.agent_schema import Meilenstein, ProposalOutput
from pmi.eval_ihp import IhpEval, _normalize_amount, evaluate
from pmi.ihp_reference import END_DATE, HOECHSTES_ANGEBOT_EUR, START_DATE, ZUSCHLAG_EUR


# ---------------------------------------------------------------------------
# Hilfsfunktion
# ---------------------------------------------------------------------------

def _output(
    *,
    budget_rahmen: str | None = "317829.00",
    meilensteine: list[dict] | None = None,
    ursache: str = "Lieferant nicht verfügbar",
    massnahme: str = "Alternativlieferant prüfen",
    verantwortlich: str = "Projektteam",
) -> ProposalOutput:
    if meilensteine is None:
        meilensteine = [
            {"bezeichnung": "Vertragsbeginn", "zieltermin": START_DATE},
            {"bezeichnung": "Vertragsende", "zieltermin": END_DATE},
        ]
    return ProposalOutput.model_validate(
        {
            "steckbrief": {
                "titel": "IHP DMS",
                "ausgangslage": "Papierverwaltung",
                "ziel": "Digitale Akte",
                "umfang": ["DMS-Einführung"],
                "nicht_umfang": [],
                "stakeholder": [{"name": "IHP GmbH", "rolle": "Auftraggeber"}],
                "meilensteine": meilensteine,
                "budget_rahmen": budget_rahmen,
                "erfolgskriterien": ["System in Betrieb"],
                "annahmen": [],
                "offene_fragen": [],
            },
            "risikoliste": {
                "risiken": [
                    {
                        "beschreibung": "Lieferverzug",
                        "ursache": ursache,
                        "auswirkung": "Projektverzögerung",
                        "wahrscheinlichkeit": "mittel",
                        "schadensausmass": "mittel",
                        "massnahme": massnahme,
                        "verantwortlich_vorschlag": verantwortlich,
                    }
                ]
            },
        }
    )


# ---------------------------------------------------------------------------
# _normalize_amount
# ---------------------------------------------------------------------------


def test_normalize_english():
    assert _normalize_amount("317829.00") == pytest.approx(317829.0)


def test_normalize_german():
    assert _normalize_amount("317.829,00") == pytest.approx(317829.0)


def test_normalize_high_german():
    assert _normalize_amount("858.074,65") == pytest.approx(858074.65)


def test_normalize_none():
    assert _normalize_amount(None) is None


def test_normalize_invalid():
    assert _normalize_amount("kein Betrag") is None


# ---------------------------------------------------------------------------
# Hartes Kriterium 1: budget_rahmen
# ---------------------------------------------------------------------------


def test_hart_pass_budget_english():
    result = evaluate(_output(budget_rahmen="317829.00"))
    assert result.hart_pass is True
    assert result.hart_details == ()


def test_hart_pass_budget_german():
    """Deutsches Zahlenformat muss akzeptiert werden (Live-Lauf-Befund L8)."""
    result = evaluate(_output(budget_rahmen="317.829,00 EUR"))
    assert result.hart_pass is True


def test_hart_fail_budget_none():
    result = evaluate(_output(budget_rahmen=None))
    assert result.hart_pass is False
    assert any("leer" in d for d in result.hart_details)


def test_hart_fail_budget_wrong_amount():
    result = evaluate(_output(budget_rahmen="1000000.00"))
    assert result.hart_pass is False
    assert any("budget_rahmen" in d for d in result.hart_details)


def test_hart_fail_budget_hoechstes_angebot():
    """858074.65 als budget_rahmen ist Fail – L8: darf nur als höchstes Angebot stehen."""
    result = evaluate(_output(budget_rahmen="858074.65"))
    assert result.hart_pass is False


def test_hart_fail_budget_hoechstes_angebot_german():
    result = evaluate(_output(budget_rahmen="858.074,65"))
    assert result.hart_pass is False


# ---------------------------------------------------------------------------
# Hartes Kriterium 2: meilensteine nicht leer
# ---------------------------------------------------------------------------


def test_hart_fail_meilensteine_leer():
    result = evaluate(_output(meilensteine=[]))
    assert result.hart_pass is False
    assert any("meilensteine" in d for d in result.hart_details)


def test_hart_pass_ein_meilenstein_mit_start():
    result = evaluate(
        _output(meilensteine=[{"bezeichnung": "Start", "zieltermin": START_DATE}])
    )
    # meilensteine nicht leer, EndDate fehlt → Fail wegen Kriterium 4
    assert result.hart_pass is False
    assert any(END_DATE in d for d in result.hart_details)


# ---------------------------------------------------------------------------
# Hartes Kriterium 3+4: beide Laufzeitdaten
# ---------------------------------------------------------------------------


def test_hart_pass_beide_daten():
    result = evaluate(_output())
    assert result.hart_pass is True
    assert START_DATE not in str(result.hart_details)
    assert END_DATE not in str(result.hart_details)


def test_hart_fail_start_fehlt():
    result = evaluate(
        _output(
            meilensteine=[{"bezeichnung": "Ende", "zieltermin": END_DATE}]
        )
    )
    assert result.hart_pass is False
    assert any(START_DATE in d for d in result.hart_details)


def test_hart_fail_end_fehlt():
    result = evaluate(
        _output(
            meilensteine=[{"bezeichnung": "Start", "zieltermin": START_DATE}]
        )
    )
    assert result.hart_pass is False
    assert any(END_DATE in d for d in result.hart_details)


# ---------------------------------------------------------------------------
# Schwach-Marker
# ---------------------------------------------------------------------------


def test_schwach_ziffer_in_ursache():
    result = evaluate(_output(ursache="Kosten steigen auf 50000 EUR"))
    assert any("ursache" in s for s in result.schwach)


def test_schwach_ziffer_in_massnahme():
    result = evaluate(_output(massnahme="Kosten auf 3 Angebote prüfen"))
    assert any("massnahme" in s for s in result.schwach)


def test_schwach_projektleitung():
    result = evaluate(_output(verantwortlich="Projektleitung"))
    assert any("verantwortlich_vorschlag" in s for s in result.schwach)


def test_schwach_floskel_massnahme():
    result = evaluate(_output(massnahme="regelmässig prüfen"))
    assert any("massnahme" in s for s in result.schwach)


def test_schwach_floskel_massnahme_neue_schreibung():
    result = evaluate(_output(massnahme="regelmäßig prüfen"))
    assert any("massnahme" in s for s in result.schwach)


def test_keine_schwach_ohne_marker():
    result = evaluate(_output())
    assert result.schwach == ()


# ---------------------------------------------------------------------------
# Kombinierter Lauf
# ---------------------------------------------------------------------------


def test_hart_pass_schwach_leer_vollstaendig():
    result = evaluate(_output())
    assert isinstance(result, IhpEval)
    assert result.hart_pass is True
    assert result.hart_details == ()
    assert result.schwach == ()

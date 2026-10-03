"""Rote Tests T01–T24: ND HHS Bed Management Closeout-Prüfer (Haltepunkt 2).

Kein Netz, kein Modell. Gegen echten Adapter und echten Evaluator.
Schema: ProposalOutput unverändert; KEY-Zeilen nur in Freitextfeldern.
"""
from __future__ import annotations

import pytest

from pmi.agent_schema import ProposalOutput
from pmi.eval_nd_bed_management import NdBedEval, evaluate
from pmi.nd_bed_management_adapter import adapt


def _proposal(
    *,
    budget_rahmen: str | None = None,
    ausgangslage: str = "Kontext ohne Kennzahlenschlüssel.",
    erfolgskriterien: list[str] | None = None,
    annahmen: list[str] | None = None,
    meilensteine: list[dict] | None = None,
    risiko_beschreibung: str = "Kein Kennzahlenschlüssel in dieser Beschreibung.",
) -> ProposalOutput:
    return ProposalOutput.model_validate(
        {
            "steckbrief": {
                "titel": "Bed Management System",
                "ausgangslage": ausgangslage,
                "ziel": "Electronic bed registry",
                "umfang": ["Bed registry"],
                "nicht_umfang": [],
                "stakeholder": [{"name": "DHHS", "rolle": "Agency"}],
                "meilensteine": meilensteine or [],
                "budget_rahmen": budget_rahmen,
                "erfolgskriterien": erfolgskriterien
                or ["Platzhalter ohne KEY-Zeile"],
                "annahmen": annahmen or [],
                "offene_fragen": [],
            },
            "risikoliste": {
                "risiken": [
                    {
                        "beschreibung": risiko_beschreibung,
                        "ursache": "u",
                        "auswirkung": "a",
                        "wahrscheinlichkeit": "mittel",
                        "schadensausmass": "mittel",
                        "massnahme": "m",
                        "verantwortlich_vorschlag": "Team",
                    }
                ]
            },
        }
    )


def _lines(**keyed: str) -> str:
    return "\n".join(f"{key}: {value}" for key, value in keyed.items())


# ---------------------------------------------------------------------------
# T01–T18 (Adapterformat)
# ---------------------------------------------------------------------------


def test_t01_exakte_kernwerte():
    out = _proposal(
        budget_rahmen=_lines(
            ORIGINAL_BASELINE_BUDGET="$490,404",
            FINAL_BASELINE_BUDGET="$627,678.14",
            ACTUAL_COST="$577,678.14",
            ACTUAL_FINISH_DATE="4/24/26",
            VENDOR="Bamboo Health",
        )
    )
    result = evaluate(out)
    assert isinstance(result, NdBedEval)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t02_formatvarianten_exakt():
    out = _proposal(
        budget_rahmen=_lines(
            ACTUAL_COST="577678.14",
            ORIGINAL_BASELINE_BUDGET="490,404.00",
            ACTUAL_FINISH_DATE="2026-04-24",
            PROJECT_START_DATE="06.01.2022",
            VENDOR="  bamboo   HEALTH ",
        )
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t03_edge_gerundet_konsistent():
    out = _proposal(
        budget_rahmen=_lines(
            ACTUAL_COST="$0.58M",
            ORIGINAL_BASELINE_BUDGET="$490K",
        )
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert "steckbrief.budget_rahmen" in result.schwach
    assert result.unbelegt == ()


def test_t04_neg_gerundet_falsch_1_2M():
    out = _proposal(budget_rahmen=_lines(ACTUAL_COST="$1.2M"))
    result = evaluate(out)
    assert result.hart_pass is False
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t05_neg_platzhalter_TBD():
    out = _proposal(budget_rahmen=_lines(ACTUAL_FINISH_DATE="TBD"))
    result = evaluate(out)
    assert result.hart_pass is False


def test_t06_neg_leerer_betrag():
    out = _proposal(budget_rahmen=_lines(FINAL_BASELINE_BUDGET=""))
    result = evaluate(out)
    assert result.hart_pass is False


def test_t07_neg_baseline_verwechselt():
    out = _proposal(budget_rahmen=_lines(ORIGINAL_BASELINE_BUDGET="$627,678.14"))
    result = evaluate(out)
    assert result.hart_pass is False


def test_t08_neg_falsche_kostenrichtung():
    out = _proposal(budget_rahmen=_lines(FINAL_COST_VARIANCE="8% over"))
    result = evaluate(out)
    assert result.hart_pass is False


def test_t09_varianz_ohne_richtung():
    out = _proposal(
        budget_rahmen=_lines(
            ORIGINAL_COST_VARIANCE="18%",
            ORIGINAL_SCHEDULE_VARIANCE="52.4%",
        )
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert "steckbrief.budget_rahmen" in result.schwach


def test_t10_neg_vendor_falsch():
    out = _proposal(budget_rahmen=_lines(VENDOR="Epic Systems"))
    result = evaluate(out)
    assert result.hart_pass is False


def test_t11_neg_scope_change_behauptet():
    out = _proposal(
        budget_rahmen=_lines(
            MAJOR_SCOPE_CHANGES="Erweiterung um Kinder- und Jugendpsychiatrie"
        )
    )
    result = evaluate(out)
    assert result.hart_pass is False


def test_t12_neg_objective_not_met():
    out = _proposal(
        budget_rahmen="OBJECTIVE_3_STATUS: Not Met\nOBJECTIVE_3_STATUS_ZITAT: "
    )
    result = evaluate(out)
    assert result.hart_pass is False


def test_t13_abgeleitete_einsparung_unbelegt():
    out = _proposal(
        budget_rahmen=_lines(ACTUAL_COST="$577,678.14"),
        ausgangslage="Das Projekt sparte $50,000 gegenüber der finalen Baseline.",
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert any(u.startswith("steckbrief.ausgangslage") for u in result.unbelegt)


def test_t14_final_baseline_start_erfunden():
    out = _proposal(budget_rahmen=_lines(FINAL_BASELINE_START_DATE="1/6/22"))
    result = evaluate(out)
    assert result.hart_pass is True
    assert any("FINAL_BASELINE_START_DATE" in u or "steckbrief.budget_rahmen" in u for u in result.unbelegt)


def test_t15_satztreffer_lessons_learned():
    out = _proposal(
        ausgangslage="The   sponsor was lower in the HHS hierarchy than most sponsors are."
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t16_objective_met_ohne_zitat():
    out = _proposal(
        budget_rahmen=(
            "OBJECTIVE_1_STATUS: Met\n"
            "OBJECTIVE_1_STATUS_ZITAT: \n"
            "OBJECTIVE_2_STATUS: erfüllt\n"
            "OBJECTIVE_2_STATUS_ZITAT: "
        )
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert any("OBJECTIVE_1" in s or "steckbrief.budget_rahmen" in s for s in result.schwach)


def test_t17_quantifizierte_wirkung_objective1():
    out = _proposal(
        ausgangslage="Der manuelle Aufwand zur Bettensuche sank um 40 %."
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert any(u.startswith("steckbrief.ausgangslage") for u in result.unbelegt)


def test_t18_edge_leere_proposal():
    """OFFEN B: fehlende Keys = nicht bewertet → leere Kennzahlen sind Pass."""
    out = _proposal()
    result = evaluate(out)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


# ---------------------------------------------------------------------------
# T19–T24 (Delta)
# ---------------------------------------------------------------------------


def test_t19_neg_key_konflikt_zwei_felder():
    out = _proposal(
        budget_rahmen=_lines(ACTUAL_COST="$577,678.14"),
        erfolgskriterien=["ACTUAL_COST: $600,000"],
    )
    result = evaluate(out)
    assert result.hart_pass is False
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t20_zahl_ohne_key_nur_aussage():
    out = _proposal(
        ausgangslage="Das Budget lag bei rund 500.000 Dollar."
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert "steckbrief.ausgangslage[0]" in result.unbelegt
    assert result.schwach == ()


def test_t21_de_format_eindeutig():
    out = _proposal(
        budget_rahmen=_lines(FINAL_BASELINE_BUDGET="627.678,14 $")
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t22_edge_de_format_mehrdeutig():
    out = _proposal(
        budget_rahmen=_lines(ORIGINAL_BASELINE_BUDGET="490.404")
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert "steckbrief.budget_rahmen" in result.schwach


def test_t23_apostroph_normalisiert():
    out = _proposal(
        ausgangslage=(
            "expended identifying available beds per patient's/client's needs."
        )
    )
    result = evaluate(out)
    assert result.hart_pass is True
    assert result.unbelegt == ()
    assert result.schwach == ()


def test_t24_neg_aussage_in_risikoliste_unbelegt():
    out = _proposal(risiko_beschreibung="Go-Live erfolgte am 1/15/26.")
    result = evaluate(out)
    assert result.hart_pass is True
    assert "risikoliste[0].beschreibung[0]" in result.unbelegt


# ---------------------------------------------------------------------------
# Adapter-Sichtbarkeit (Konflikt / Herkunft)
# ---------------------------------------------------------------------------


def test_adapt_exposed_for_evaluator():
    """Adapter ist der vorgesehene Einstieg; Stub wirft bis zur Implementierung."""
    out = _proposal(budget_rahmen=_lines(VENDOR="Bamboo Health"))
    with pytest.raises(NotImplementedError):
        adapt(out)

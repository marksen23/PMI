"""Prüfer für die Kurzfassung Justitia 4.0. Kein Netz, kein Modell."""
from datetime import date

from pmi.agent_schema import Meilenstein, ProposalOutput, Stakeholder
from pmi.eval_justitia import evaluate
from pmi.justitia_reference import KAPITEL7_SAETZE


def _output(
    beschreibungen: list[str],
    *,
    meilensteine: list[Meilenstein] | None = None,
    budget_rahmen: str | None = None,
    ursache: str = "im Text nicht ausgeführt",
    auswirkung: str = "im Text nicht ausgeführt",
    massnahme: str = "im Text nicht ausgeführt",
    verantwortlich: str = "im Text nicht ausgeführt",
    wahrscheinlichkeit: str = "mittel",
    schadensausmass: str = "mittel",
) -> ProposalOutput:
    risiken = []
    for beschreibung in beschreibungen:
        risiken.append(
            {
                "beschreibung": beschreibung,
                "ursache": ursache,
                "auswirkung": auswirkung,
                "wahrscheinlichkeit": wahrscheinlichkeit,
                "schadensausmass": schadensausmass,
                "massnahme": massnahme,
                "verantwortlich_vorschlag": verantwortlich,
            }
        )
    return ProposalOutput.model_validate(
        {
            "steckbrief": {
                "titel": "Justitia 4.0",
                "ausgangslage": "Papier",
                "ziel": "Digitalisierung",
                "umfang": ["Zivilverfahren"],
                "nicht_umfang": [],
                "stakeholder": [Stakeholder(name="KKJPD", rolle="Auftraggeber").model_dump()],
                "meilensteine": [m.model_dump() for m in (meilensteine or [])],
                "budget_rahmen": budget_rahmen,
                "erfolgskriterien": ["elektronische Akte"],
                "annahmen": [],
                "offene_fragen": [],
            },
            "risikoliste": {"risiken": risiken},
        }
    )


def test_harte_kriterien_passen_bei_leerem_plan_und_budget():
    result = evaluate(_output([KAPITEL7_SAETZE[0]]))
    assert result.hart_pass is True


def test_meilenstein_ist_hart_fail():
    meilenstein = Meilenstein(bezeichnung="Kick-off", zieltermin=date(2019, 1, 1))
    result = evaluate(_output([KAPITEL7_SAETZE[0]], meilensteine=[meilenstein]))
    assert result.hart_pass is False


def test_budget_ist_hart_fail_auch_als_nutzenzahl():
    result = evaluate(
        _output([KAPITEL7_SAETZE[0]], budget_rahmen="ca. CHF 18,5 Millionen")
    )
    assert result.hart_pass is False


def test_beschreibung_gleich_satz_ist_belegt():
    result = evaluate(_output([KAPITEL7_SAETZE[0]]))
    assert result.unbelegte_indizes == ()
    assert 0 in result.treffer[0]
    assert 0 not in result.doppelte_saetze


def test_kleinschreibung_und_teilsatz_treffen():
    satz = KAPITEL7_SAETZE[0]
    klein = satz[:1].lower() + satz[1:]
    teilsatz = "handlungsunfähig"
    assert teilsatz in satz
    klein_result = evaluate(_output([klein]))
    teil_result = evaluate(_output([teilsatz]))
    assert klein_result.unbelegte_indizes == ()
    assert 0 in klein_result.treffer[0]
    assert teil_result.unbelegte_indizes == ()
    assert 0 in teil_result.treffer[0]


def test_leere_beschreibung_ist_unbelegt():
    result = evaluate(_output(["   "]))
    assert result.unbelegte_indizes == (0,)
    assert result.treffer[0] == ()


def test_fremde_beschreibung_ist_unbelegt():
    result = evaluate(_output(["Widerstand der Hosting-Anbieter ausserhalb der Liste"]))
    assert result.unbelegte_indizes == (0,)


def test_doppelt_und_fehlend_ist_qualitaetsvermerk():
    result = evaluate(_output([KAPITEL7_SAETZE[0], KAPITEL7_SAETZE[0]]))
    assert result.unbelegte_indizes == ()
    assert 0 in result.doppelte_saetze
    assert 1 in result.fehlende_saetze


def test_floskel_in_massnahme_ist_schwach():
    result = evaluate(
        _output([KAPITEL7_SAETZE[0]], massnahme="Die Lage regelmässig prüfen")
    )
    assert "risiken[0].massnahme" in result.schwach
    assert result.hart_pass is True


def test_ziffer_in_ursache_ist_schwach():
    result = evaluate(_output([KAPITEL7_SAETZE[0]], ursache="Verzug um 8 Jahre"))
    assert "risiken[0].ursache" in result.schwach


def test_wahrscheinlichkeit_wird_nur_ergaenzt_markiert():
    result = evaluate(_output([KAPITEL7_SAETZE[0]], wahrscheinlichkeit="hoch"))
    assert "risiken[0].wahrscheinlichkeit" in result.ergaenzt
    assert "risiken[0].schadensausmass" in result.ergaenzt
    assert result.hart_pass is True
    assert "risiken[0].wahrscheinlichkeit" not in result.schwach

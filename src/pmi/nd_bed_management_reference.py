"""Hardfacts aus ND HHS Bed Management Closeout 27.5166.02000.

Kein Modell, kein Netz. Werte wortgleich aus der freigegebenen REFERENZLISTE.
"""
from __future__ import annotations

# Kennzahlenschlüssel (Adapter erkennt nur diese KEY-Zeilen).
REFERENCE_KEYS: frozenset[str] = frozenset(
    {
        "REPORT_SUBMITTED_DATE",
        "PROJECT_NAME",
        "AGENCY_NAME",
        "PROJECT_SPONSOR",
        "PROJECT_MANAGER",
        "VENDOR",
        "SOLUTION_TYPE",
        "LEGISLATIVE_BASIS",
        "PROJECT_START_DATE",
        "ORIGINAL_BASELINE_END_DATE",
        "ORIGINAL_BASELINE_BUDGET",
        "FINAL_BASELINE_START_DATE",
        "FINAL_BASELINE_END_DATE",
        "FINAL_BASELINE_BUDGET",
        "FUNDING_SOURCE",
        "FUNDING_STATE_SHARE",
        "FUNDING_FEDERAL_SHARE",
        "ACTUAL_FINISH_DATE",
        "ACTUAL_COST",
        "ORIGINAL_SCHEDULE_VARIANCE",
        "FINAL_SCHEDULE_VARIANCE",
        "ORIGINAL_COST_VARIANCE",
        "FINAL_COST_VARIANCE",
        "MAJOR_SCOPE_CHANGES",
        "OBJECTIVE_1",
        "OBJECTIVE_1_STATUS",
        "OBJECTIVE_1_OUTCOME",
        "OBJECTIVE_2",
        "OBJECTIVE_2_STATUS",
        "OBJECTIVE_2_OUTCOME",
        "OBJECTIVE_3",
        "OBJECTIVE_3_STATUS",
        "OBJECTIVE_3_OUTCOME",
        "TEMPLATE_VERSION",
    }
)

ORIGINAL_BASELINE_BUDGET = "490404.00"
FINAL_BASELINE_BUDGET = "627678.14"
ACTUAL_COST = "577678.14"
PROJECT_START_DATE = "2022-01-06"
ORIGINAL_BASELINE_END_DATE = "2024-01-22"
FINAL_BASELINE_END_DATE = "2024-12-06"
ACTUAL_FINISH_DATE = "2026-04-24"
ORIGINAL_SCHEDULE_VARIANCE = 52
FINAL_SCHEDULE_VARIANCE = 32
ORIGINAL_COST_VARIANCE_PCT = 18
ORIGINAL_COST_VARIANCE_DIR = "over"
FINAL_COST_VARIANCE_PCT = 8
FINAL_COST_VARIANCE_DIR = "under"
VENDOR = "Bamboo Health"
MAJOR_SCOPE_CHANGES_OK = frozenset({"none", "keine"})
OBJECTIVE_STATUS_OK = frozenset({"met", "erfüllt", "erreicht"})
OBJECTIVE_STATUS_FAIL = frozenset({"not met", "nicht erfüllt", "nicht erreicht"})
FUNDING_STATE_SHARE = 10
FUNDING_FEDERAL_SHARE = 90

# Sätze für ZUORDNUNG (wortgleich aus dem Auszug, nach Apostroph-Normalisierung vergleichbar).
SOURCE_SENTENCES: tuple[str, ...] = (
    "Accurate bed availability reporting for service providers and crisis staff with decreased manual effort determining if facility meets patient/client needs.",
    "At project Execution start, a qualitative assessment will be formulated to determine time expended identifying available beds per patient's/client's needs.",
    "An ending assessment was not taken as go live extended for over a year. The improvement however has been met with the dashboard showing bed availability",
    "Statewide bed utilization data and reports are available to authorized stakeholders",
    "Staff are using the reporting features of the new system",
    "95% of targeted facilities report bed availability data.",
    "The post measurements were not completed as originated. Currently the vendor dashboard shows 99% of facilities are reporting into the new system",
    "Adequate time to achieve all deliverables in a timely manner could have been better. Ensuring all known stakeholders are involved early could have helped with this.",
    "Expectations from IT and HHS could be more clearly defined and perhaps prevent some project delays. When working with non state entities, state needs them to buy in to the project effort and be brought in as a stakeholder",
    "The sponsor was lower in the HHS hierarchy than most sponsors are. This caused some decisions taking a bit longer.",
    "This was compounded by HHS staff turnover.",
    "The sponsor was lower in the HHS hierarchy than most sponsors are. This gave a true user/admin view of needs for HHS and daily use of the software.",
)

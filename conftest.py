"""Root-level conftest: setzt PMI_AUTHORIZATION_HMAC_SECRET und PYTHONPATH vor dem Import von config."""
import json
import os
from pathlib import Path

_repo_root = str(Path(__file__).parent)
_src_dir = str(Path(__file__).parent / "src")

os.environ.setdefault("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-for-pytest")

# E1: Temporal-/Activity-Tests nutzen FakeLLMProvider statt Netzaufruf.
os.environ.setdefault(
    "PMI_FAKE_LLM_JSON",
    json.dumps(
        {
            "steckbrief": {
                "titel": "Test-Projekt",
                "ausgangslage": "Ist-Zustand",
                "ziel": "Soll-Zustand",
                "umfang": ["Modul A"],
                "nicht_umfang": [],
                "stakeholder": [{"name": "Max", "rolle": "PL"}],
                "meilensteine": [],
                "budget_rahmen": None,
                "erfolgskriterien": ["Kriterium 1"],
                "annahmen": [],
                "offene_fragen": [],
            },
            "risikoliste": {
                "risiken": [
                    {
                        "beschreibung": "Tech-Risiko",
                        "ursache": "Komplexität",
                        "auswirkung": "Verzögerung",
                        "wahrscheinlichkeit": "mittel",
                        "schadensausmass": "mittel",
                        "massnahme": "Prototyp",
                        "verantwortlich_vorschlag": "Tech Lead",
                    }
                ]
            },
        }
    ),
)

# Stellt sicher dass Worker-Subprozesse sowohl 'config' als auch 'pmi' importieren können.
_existing = os.environ.get("PYTHONPATH", "")
_paths = [p for p in [_repo_root, _src_dir] if p not in _existing.split(os.pathsep)]
if _paths:
    os.environ["PYTHONPATH"] = os.pathsep.join(_paths + ([_existing] if _existing else []))

from hypothesis import HealthCheck, settings

settings.register_profile(
    "pmi_tests",
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,  # git-Operationen überschreiten den 200ms-Standard-Deadline
)
settings.load_profile("pmi_tests")

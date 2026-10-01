"""Root-level conftest: setzt PMI_AUTHORIZATION_HMAC_SECRET und PYTHONPATH vor dem Import von config."""
import os
from pathlib import Path

_repo_root = str(Path(__file__).parent)
_src_dir = str(Path(__file__).parent / "src")

os.environ.setdefault("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret-for-pytest")

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

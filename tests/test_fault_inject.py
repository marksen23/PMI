"""
Belegt: Fault-Injection greift nur wenn explizit aktiviert.
Ohne PMI_FAULT_INJECT darf kein maybe_crash()-Aufruf abbrechen.
"""
import os

import pytest

from pmi.fault_inject import maybe_crash


def test_no_crash_without_activation():
    """maybe_crash() mit verschiedenen Punkten: kein Abbruch ohne Aktivierung."""
    env_before = os.environ.get("PMI_FAULT_INJECT")
    try:
        os.environ.pop("PMI_FAULT_INJECT", None)
        # Alle bekannten Fault-Punkte dürfen ohne Aktivierung nichts auslösen
        for point in (
            "pre_start_persist",
            "post_start_pre_write",
            "pre_rename",
            "post_rename_pre_commit",
            "post_commit",
            "mid_authorization",
        ):
            maybe_crash(point)  # Darf nicht abbrechen
    finally:
        if env_before is not None:
            os.environ["PMI_FAULT_INJECT"] = env_before
        else:
            os.environ.pop("PMI_FAULT_INJECT", None)


def test_crash_activates_with_matching_point(tmp_path):
    """maybe_crash() bricht nur ab wenn PMI_FAULT_INJECT exakt übereinstimmt."""
    import subprocess
    import sys

    # Subprozess mit PMI_FAULT_INJECT gesetzt → muss mit Exit-Code != 0 enden
    env = os.environ.copy()
    env["PMI_FAULT_INJECT"] = "test_point"
    env["PMI_AUTHORIZATION_HMAC_SECRET"] = env.get("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret")
    result = subprocess.run(
        [sys.executable, "-c",
         "from pmi.fault_inject import maybe_crash; maybe_crash('test_point')"],
        env=env,
    )
    assert result.returncode != 0, "maybe_crash() muss den Prozess bei aktiviertem Punkt beenden"


def test_no_crash_for_different_point(tmp_path):
    """maybe_crash('A') greift nicht wenn PMI_FAULT_INJECT='B' gesetzt ist."""
    import subprocess
    import sys

    env = os.environ.copy()
    env["PMI_FAULT_INJECT"] = "point_B"
    env["PMI_AUTHORIZATION_HMAC_SECRET"] = env.get("PMI_AUTHORIZATION_HMAC_SECRET", "test-secret")
    result = subprocess.run(
        [sys.executable, "-c",
         "from pmi.fault_inject import maybe_crash; maybe_crash('point_A'); print('ok')"],
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, "maybe_crash('A') darf nicht auslösen wenn PMI_FAULT_INJECT='B'"
    assert b"ok" in result.stdout

"""Pure Risk-Derivation-Funktion. Keine Seiteneffekte, kein DB-Zugriff."""
from enum import Enum

import config

_OUTSIDE_SCOPES = {"global", "remote", "external"}


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    INVALID = "UNGÜLTIG"


def derive_risk(proposal: dict) -> RiskLevel:
    scope = proposal.get("scope", "")

    # PROPOSAL_ONLY mit Außenwirkung → UNGÜLTIG
    proposal_only = proposal.get("proposal_only", False)
    if proposal_only and scope in _OUTSIDE_SCOPES:
        return RiskLevel.INVALID

    # Kosten-Kriterium: deaktiviert (PROVISIONAL)
    if config.COST_THRESHOLD_ENABLED:
        cost = proposal.get("estimated_cost", 0)
        if cost > 100:
            return RiskLevel.R2

    if scope in _OUTSIDE_SCOPES:
        return RiskLevel.R2

    action = proposal.get("action", "")
    if action in ("delete_file", "overwrite_file"):
        return RiskLevel.R1

    return RiskLevel.R0

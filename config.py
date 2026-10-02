# Offene Entscheidungen (Skizze Abschnitt 12) – alle als PROVISIONAL markiert.
# Keine dieser Konstanten darf ohne explizite Freigabe von Mark geändert werden.

AUTHORIZATION_TTL_SECONDS: int = 86400  # PROVISIONAL – 24 Stunden

MAX_PROPOSAL_VERSIONS: int | None = None  # PROVISIONAL – unbegrenzt

COST_THRESHOLD_ENABLED: bool = False  # PROVISIONAL – deaktiviert

DIVERGENT_RECONCILIATION_STRATEGY: str = "none"  # PROVISIONAL – keine automatische Reconciliation in Phase 1

import os as _os

# HMAC-Schlüssel zur Signierung von Authorization-Datensätzen.
# Muss als Umgebungsvariable PMI_AUTHORIZATION_HMAC_SECRET gesetzt sein.
# begin_execution() prüft die Signatur; rohes INSERT ohne gültigen HMAC wird abgewiesen.
_raw_secret = _os.environ.get("PMI_AUTHORIZATION_HMAC_SECRET", "")
if not _raw_secret:
    raise RuntimeError(
        "PMI_AUTHORIZATION_HMAC_SECRET ist nicht gesetzt. "
        "Die App kann ohne HMAC-Schlüssel nicht starten."
    )
AUTHORIZATION_HMAC_SECRET: bytes = _raw_secret.encode()  # PROVISIONAL – Format

# LLM-Agent-Konfiguration (Auftrag 3).
# Schlüssel kommt aus der Umgebung – niemals im Repository oder im Audit ablegen.
AGENT_MODEL: str = "claude-opus-5-5"  # PROVISIONAL – finales Modell noch nicht freigegeben
ANTHROPIC_API_KEY: str = _os.environ.get("ANTHROPIC_API_KEY", "")

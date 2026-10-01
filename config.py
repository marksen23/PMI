# Offene Entscheidungen (Skizze Abschnitt 12) – alle als PROVISIONAL markiert.
# Keine dieser Konstanten darf ohne explizite Freigabe von Mark geändert werden.

CANONICAL_JSON_SORT_KEYS: bool = True  # PROVISIONAL
CANONICAL_JSON_SEPARATORS: tuple[str, str] = (",", ":")  # PROVISIONAL

AUTHORIZATION_TTL_SECONDS: int = 86400  # PROVISIONAL – 24 Stunden

MAX_PROPOSAL_VERSIONS: int | None = None  # PROVISIONAL – unbegrenzt

COST_THRESHOLD_ENABLED: bool = False  # PROVISIONAL – deaktiviert

DIVERGENT_RECONCILIATION_STRATEGY: str = "none"  # PROVISIONAL – keine automatische Reconciliation in Phase 1

# HMAC-Schlüssel zur Signierung von Authorization-Datensätzen.
# begin_execution() prüft die Signatur; rohes INSERT ohne gültigen HMAC wird abgewiesen.
AUTHORIZATION_HMAC_SECRET: bytes = b"provisional-dev-secret"  # PROVISIONAL

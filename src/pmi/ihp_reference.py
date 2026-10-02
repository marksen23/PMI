"""Hardfacts aus TED 274205-2025 (IHP DMS). Kein Modell, kein Netz.

Nur dieser Bescheid. Alle Werte exakt wie im Dokument.
"""

ZUSCHLAG_EUR: float = 317829.00
"""PayableAmount / LowerTenderAmount – der vergebene Auftragswert."""

HOECHSTES_ANGEBOT_EUR: float = 858074.65
"""HigherTenderAmount – darf nur als höchstes Angebot erscheinen, nie als budget_rahmen."""

START_DATE: str = "2024-12-20"
"""StartDate der Vertragslaufzeit."""

END_DATE: str = "2028-12-31"
"""EndDate der Vertragslaufzeit."""

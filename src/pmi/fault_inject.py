"""
Fault-Injection-Punkte für Crashtests.

Nur aktiv wenn PMI_FAULT_INJECT=<point> gesetzt ist (niemals in Produktion).
Produktionscode ruft maybe_crash("<point>") an kritischen Stellen auf.
"""
import os


def maybe_crash(point: str) -> None:
    if os.environ.get("PMI_FAULT_INJECT") == point:
        os._exit(1)

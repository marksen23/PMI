"""Agent-Stub. Kein LLM. Erzeugt schemavalide Proposals ohne Steuerfelder."""

_FORBIDDEN_FIELDS = {
    "status",
    "decided_by",
    "authorization_id",
    "execution_id",
    "risk_level",
}

_ALLOWED_FIELDS = {"action", "path", "scope", "description"}


class ValidationError(ValueError):
    pass


def create_proposal(spec: dict) -> dict:
    """Validiert spec und gibt ein schemakonformes Proposal zurück."""
    forbidden_found = set(spec.keys()) & _FORBIDDEN_FIELDS
    if forbidden_found:
        raise ValidationError(f"Unerlaubte Steuerfelder im Proposal: {forbidden_found}")

    unknown = set(spec.keys()) - _ALLOWED_FIELDS
    if unknown:
        raise ValidationError(f"Unbekannte Felder: {unknown}")

    required = {"action", "path", "scope"}
    missing = required - set(spec.keys())
    if missing:
        raise ValidationError(f"Pflichtfelder fehlen: {missing}")

    return dict(spec)

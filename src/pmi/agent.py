"""Agent: LLM-Aufruf → validiertes ProposalOutput → Audit + insert_proposal.

create_proposal() bleibt als Validierungshilfe für nicht-LLM-Proposals erhalten
(direkte Systemvorschläge mit action/path/scope-Schema).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod

from pydantic import ValidationError as PydanticValidationError

from pmi.agent_schema import ProposalOutput
from pmi.db import append_audit, insert_proposal, _canonical_json

_PROMPT_TEMPLATE_VERSION = "1"

_PROMPT_TEMPLATE = """\
Du bist ein Projektmanagement-Assistent. Erstelle einen Projektsteckbrief und eine Risikoliste \
für folgende Aufgabe:

{task}

Antworte ausschließlich mit einem JSON-Objekt im folgenden Schema:
{{
  "steckbrief": {{
    "titel": "<string>",
    "ausgangslage": "<string>",
    "ziel": "<string, nicht leer>",
    "umfang": ["<string>", ...],
    "nicht_umfang": ["<string>", ...],
    "stakeholder": [{{"name": "<string>", "rolle": "<string>"}}],
    "meilensteine": [{{"bezeichnung": "<string>", "zieltermin": "<ISO-Datum oder null>"}}],
    "budget_rahmen": "<string oder null>",
    "erfolgskriterien": ["<string>", ...],
    "annahmen": ["<string>", ...],
    "offene_fragen": ["<string>", ...]
  }},
  "risikoliste": {{
    "risiken": [
      {{
        "beschreibung": "<string>",
        "ursache": "<string>",
        "auswirkung": "<string>",
        "wahrscheinlichkeit": "niedrig|mittel|hoch",
        "schadensausmass": "niedrig|mittel|hoch",
        "massnahme": "<string>",
        "verantwortlich_vorschlag": "<Rolle>"
      }}
    ]
  }}
}}
Keine weiteren Felder außer den obigen. 1–15 Risiken.
"""


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, model: str, version: str) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...


class FakeLLMProvider(LLMProvider):
    """Deterministischer Fake-Provider für Tests."""

    def __init__(self, fixed_response: str, model: str = "fake-model", version: str = "0") -> None:
        self._response = fixed_response
        self._model = model
        self._version = version

    def generate(self, prompt: str, model: str, version: str) -> str:
        return self._response

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version


def _build_prompt(task: str) -> str:
    return _PROMPT_TEMPLATE.format(task=task)


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_agent(
    task: str,
    conn: sqlite3.Connection,
    provider: LLMProvider,
) -> tuple[str, ProposalOutput]:
    """
    Führt einen LLM-Aufruf durch, validiert die Ausgabe und speichert Proposal + Audit.

    Returns:
        (proposal_id, validated_output)
    """
    model = provider.model
    version = provider.version

    prompt = _build_prompt(task)
    prompt_with_version = prompt + f"\n\n# prompt_template_version={_PROMPT_TEMPLATE_VERSION}"
    input_hash = _sha256_str(prompt_with_version)
    proposal_input_hash = _sha256_str(task)

    raw_output = provider.generate(prompt_with_version, model, version)
    output_hash = _sha256_str(raw_output)

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM-Ausgabe ist kein gültiges JSON: {exc}") from exc

    validated: ProposalOutput = ProposalOutput.model_validate(parsed)

    validated_output_hash = _sha256_bytes(_canonical_json(validated.model_dump(mode="json")))

    payload = validated.model_dump(mode="json")
    proposal_id = insert_proposal(conn, payload=payload, proposed_by="agent:llm")

    dedup = f"agent_proposal:{proposal_id}"
    append_audit(
        conn,
        dedup_key=dedup,
        event_type="AGENT_PROPOSAL_GENERATED",
        execution_id=None,
        seq=0,
        payload={
            "proposal_id": proposal_id,
            "model": model,
            "version": version,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "validated_output_hash": validated_output_hash,
            "proposal_input_hash": proposal_input_hash,
        },
    )

    return proposal_id, validated


# ---------------------------------------------------------------------------
# Legacy: direkte Proposals mit action/path/scope-Schema
# ---------------------------------------------------------------------------

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
    """Validiert spec (action/path/scope-Schema) und gibt ein schemakonformes Proposal zurück."""
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

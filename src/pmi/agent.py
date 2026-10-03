"""Agent: LLM-Aufruf → validiertes ProposalOutput → Audit + insert_proposal.

create_proposal() bleibt als Validierungshilfe für nicht-LLM-Proposals erhalten
(direkte Systemvorschläge mit action/path/scope-Schema).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import ValidationError as PydanticValidationError

from pmi.agent_schema import ProposalOutput
from pmi.agent_store import compute_expected_hash
from pmi.db import append_audit, insert_proposal

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


@dataclass(frozen=True)
class GeneratedProposal:
    """LLM-Ergebnis nach Validierung – noch nicht in der DB."""

    proposal_id: str
    output: ProposalOutput
    model: str
    version: str
    input_hash: str
    output_hash: str
    validated_output_hash: str
    proposal_input_hash: str

    @property
    def payload(self) -> dict:
        return self.output.model_dump(mode="json")


def generate_proposal(task: str, provider: LLMProvider) -> GeneratedProposal:
    """A1: LLM-Aufruf + Validierung ohne DB-Writes."""
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
    validated_output_hash = compute_expected_hash(validated)

    return GeneratedProposal(
        proposal_id=str(uuid.uuid4()),
        output=validated,
        model=model,
        version=version,
        input_hash=input_hash,
        output_hash=output_hash,
        validated_output_hash=validated_output_hash,
        proposal_input_hash=proposal_input_hash,
    )


def persist_generated_proposal(
    conn: sqlite3.Connection,
    generated: GeneratedProposal,
    *,
    commit: bool = True,
) -> str:
    """Schreibt Proposal + AGENT_PROPOSAL_GENERATED (ohne DRAFT_PROPOSAL)."""
    insert_proposal(
        conn,
        payload=generated.payload,
        proposed_by="agent:llm",
        proposal_id=generated.proposal_id,
        commit=commit,
    )
    append_audit(
        conn,
        dedup_key=f"agent_proposal:{generated.proposal_id}",
        event_type="AGENT_PROPOSAL_GENERATED",
        execution_id=None,
        seq=0,
        payload={
            "proposal_id": generated.proposal_id,
            "model": generated.model,
            "version": generated.version,
            "input_hash": generated.input_hash,
            "output_hash": generated.output_hash,
            "validated_output_hash": generated.validated_output_hash,
            "proposal_input_hash": generated.proposal_input_hash,
        },
        commit=commit,
    )
    return generated.proposal_id


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
    generated = generate_proposal(task, provider)
    persist_generated_proposal(conn, generated, commit=True)
    return generated.proposal_id, generated.output


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

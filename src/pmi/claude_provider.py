"""ClaudeLLMProvider: Anthropic Claude hinter dem LLMProvider-ABC.

Sicherheitsinvarianten:
- API-Key kommt ausschließlich aus der Umgebung (config.ANTHROPIC_API_KEY).
- Kein Rohtext landet im Audit (das übernimmt run_agent via input_hash/output_hash).
- Erzeugt keine Authorization, setzt keinen Status.
"""
from __future__ import annotations

import anthropic

import config
from pmi.agent import LLMProvider


class ClaudeLLMProvider(LLMProvider):
    """Ruft die Anthropic Claude Messages API auf."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or config.ANTHROPIC_API_KEY or None
        self._client = anthropic.Anthropic(api_key=key)
        self._model_id = config.AGENT_MODEL
        self._resolved_version: str | None = None

    @property
    def model(self) -> str:
        return self._model_id

    @property
    def version(self) -> str:
        # Nach dem ersten generate()-Aufruf enthält dies das vom Server
        # zurückgemeldete Modell (z.B. "claude-opus-5-5-20251101").
        return self._resolved_version or self._model_id

    def generate(self, prompt: str, model: str, version: str) -> str:
        response = self._client.messages.create(
            model=self._model_id,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        self._resolved_version = response.model
        return response.content[0].text  # type: ignore[union-attr]

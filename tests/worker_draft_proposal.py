"""
Subprozess-Worker für Auftrag-7b B1 (SIGKILL während draft_proposal).

Aufruf: python tests/worker_draft_proposal.py <db_path> <workflow_id> <call_count_path>
PMI_FAULT_INJECT=<point> optional.
PMI_FAKE_LLM_JSON muss gesetzt sein (FakeLLM).

Fault-Points (nach Implementierung in activities/agent):
  after_draft_proposal_insert  – nach Proposal-Zeile, vor AGENT-Audit
  after_draft_agent_audit      – nach AGENT-Audit, vor DRAFT_PROPOSAL-Audit
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "Usage: worker_draft_proposal.py <db_path> <workflow_id> <call_count_path>",
            file=sys.stderr,
        )
        sys.exit(2)

    db_path, workflow_id, call_count_path = sys.argv[1], sys.argv[2], sys.argv[3]

    # Zählerdatei für Provider-Aufrufe über Prozessgrenzen
    count_file = Path(call_count_path)
    if not count_file.exists():
        count_file.write_text("0", encoding="utf-8")

    from pmi.agent import FakeLLMProvider
    from pmi.temporal.activities import (
        DraftProposalInput,
        activity_draft_proposal,
        get_llm_provider,
    )

    fake_json = os.environ.get("PMI_FAKE_LLM_JSON", "")
    if not fake_json:
        print("PMI_FAKE_LLM_JSON fehlt", file=sys.stderr)
        sys.exit(2)

    class CountingFake(FakeLLMProvider):
        def generate(self, prompt: str, model: str, version: str) -> str:
            n = int(count_file.read_text(encoding="utf-8") or "0")
            count_file.write_text(str(n + 1), encoding="utf-8")
            return super().generate(prompt, model, version)

    provider = CountingFake(fake_json)

    import pmi.temporal.activities as activities

    activities.get_llm_provider = lambda: provider  # type: ignore[assignment]

    async def _run() -> None:
        await activity_draft_proposal(
            DraftProposalInput(
                task="worker-draft-atomicity",
                db_path=db_path,
                step=1,
                workflow_id=workflow_id,
            )
        )

    asyncio.run(_run())
    sys.exit(0)


if __name__ == "__main__":
    main()

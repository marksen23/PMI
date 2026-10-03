# PMI

KI-Projektmanagement-Plattform (Phase 1): Kontroll-DB (SQLite) + git-CAS + Temporal-InitiationWorkflow.

## Lokal starten

1. Temporal (Port 7233; Compose nutzt `network_mode: host`):

```bash
docker compose up -d postgresql temporal temporal-ui
```

2. Umgebungsvariablen (Mindestmenge):

```bash
export PMI_AUTHORIZATION_HMAC_SECRET="dev-secret"
export PMI_TEMPORAL_HOST="localhost:7233"   # PROVISIONAL Default
export PMI_TASK_QUEUE="pmi-initiation"      # PROVISIONAL Default
export PMI_DB_PATH="$PWD/pmi_control.db"
export PMI_REPO_PATH="$PWD"
# Demo ohne Live-LLM:
export PMI_ALLOW_FAKE_LLM=1
export PMI_FAKE_LLM_JSON='…'   # gültiges ProposalOutput-JSON
```

3. Package + Worker:

```bash
pip install -e ".[dev]"
pmi init
pmi worker   # eigener Terminal; liest Host/Queue aus ENV (D1/G2)
```

Optional Worker im Compose-Netz: `docker compose --profile worker up -d pmi-worker`.

## Bedienung (CLI-Bedienpfad)

```bash
pmi init                              # Schema an PMI_DB_PATH
pmi start "<Aufgabe>"                 # Workflow + ExecutionRequest (IDs auf stdout)
pmi show <request_id>                 # Proposal-JSON
pmi approve <request_id> <person>     # Authorization (nur human:)
pmi run <request_id>                  # Signal authorized → Apply
pmi status <request_id>               # Request-Status (+ Execution falls vorhanden)
pmi eval justitia|ihp|nd_bed <request_id>   # Report, keine Auth/Status-Änderung
pmi worker                            # Temporal-Worker
```

Kurzpfad FakeLLM-Durchstich: `make demo` (siehe `scripts/demo.sh`).

## Live-E2E (echtes Claude + Temporal)

Voraussetzungen: Docker, `ANTHROPIC_API_KEY`, `PMI_AUTHORIZATION_HMAC_SECRET`.
Optional: `ANTHROPIC_WORKSPACE_ID`. **Kein** `PMI_ALLOW_FAKE_LLM` / `PMI_FAKE_LLM_JSON`.

```bash
export ANTHROPIC_API_KEY="…"
export PMI_AUTHORIZATION_HMAC_SECRET="…"
# optional: export ANTHROPIC_WORKSPACE_ID="…"
bash scripts/live_demo.sh
```

Das Skript startet Temporal per `docker compose`, Worker ohne FakeLLM,
dann `pmi start` → `show` → `approve` → `run` → Status APPLIED und Steckbrief im Store.
Optional vorher eval: `PMI_LIVE_EVAL=1 bash scripts/live_demo.sh`.

## Tests

```bash
export PMI_AUTHORIZATION_HMAC_SECRET=test-secret-for-pytest
pytest
```

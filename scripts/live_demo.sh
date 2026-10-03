#!/usr/bin/env bash
# Auftrag 9 / A1: Live-E2E mit echtem Claude + echtem Temporal (kein FakeLLM).
# Voraussetzungen: docker compose, ANTHROPIC_API_KEY, PMI_AUTHORIZATION_HMAC_SECRET.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "live_demo: ANTHROPIC_API_KEY fehlt" >&2
  exit 2
fi
if [[ -z "${PMI_AUTHORIZATION_HMAC_SECRET:-}" ]]; then
  echo "live_demo: PMI_AUTHORIZATION_HMAC_SECRET fehlt" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "live_demo: docker fehlt (B1: docker compose Pflicht)" >&2
  exit 2
fi

LIVE_DIR="${PMI_LIVE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/pmi-live.XXXXXX")}"
DB_PATH="${LIVE_DIR}/control.db"
REPO_PATH="${LIVE_DIR}/repo"
mkdir -p "$REPO_PATH"
git -C "$REPO_PATH" init -q
git -C "$REPO_PATH" config user.email "live@example.com"
git -C "$REPO_PATH" config user.name "PMI Live"
git -C "$REPO_PATH" commit --allow-empty -qm "init"

export PMI_DB_PATH="$DB_PATH"
export PMI_REPO_PATH="$REPO_PATH"
export PMI_TEMPORAL_HOST="${PMI_TEMPORAL_HOST:-localhost:7233}"
export PMI_TASK_QUEUE="${PMI_TASK_QUEUE:-pmi-initiation}"
export PMI_START_WAIT_SECONDS="${PMI_START_WAIT_SECONDS:-180}"
export PYTHONPATH="${ROOT}:${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

# L2: FakeLLM bewusst aus
unset PMI_ALLOW_FAKE_LLM || true
unset PMI_FAKE_LLM_JSON || true

COMPOSE_STARTED=0
WORKER_PID=""
cleanup() {
  if [[ -n "${WORKER_PID}" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  if [[ "$COMPOSE_STARTED" -eq 1 && "${PMI_LIVE_KEEP_COMPOSE:-}" != "1" ]]; then
    docker compose -f "$ROOT/docker-compose.yml" stop temporal temporal-ui postgresql >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Live-Verzeichnis: $LIVE_DIR"
echo "Temporal: $PMI_TEMPORAL_HOST  Queue: $PMI_TASK_QUEUE"
echo "FakeLLM: aus (ALLOW unset, JSON unset)"

# L1: Temporal via docker compose
docker compose -f "$ROOT/docker-compose.yml" up -d postgresql temporal
COMPOSE_STARTED=1

echo "Warte auf Temporal :7233 …"
for _ in $(seq 1 90); do
  if (echo >/dev/tcp/127.0.0.1/7233) >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! (echo >/dev/tcp/127.0.0.1/7233) >/dev/null 2>&1; then
  echo "live_demo: Temporal nicht erreichbar auf localhost:7233" >&2
  docker compose -f "$ROOT/docker-compose.yml" ps >&2 || true
  exit 1
fi

pmi init

pmi worker &
WORKER_PID=$!
sleep 3
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "live_demo: Worker ist beendet" >&2
  exit 1
fi

TASK="${PMI_LIVE_TASK:-Live-E2E: kurzer Projektsteckbrief für ein internes Tool zur Aufgabensteuerung. Minimal, ohne echte Personendaten.}"
START_OUT="$(pmi start "$TASK")"
echo "$START_OUT"
REQUEST_ID="$(echo "$START_OUT" | sed -n 's/^request_id=//p')"
WORKFLOW_ID="$(echo "$START_OUT" | sed -n 's/^workflow_id=//p')"
PROPOSAL_ID="$(echo "$START_OUT" | sed -n 's/^proposal_id=//p')"
if [[ -z "$REQUEST_ID" || -z "$WORKFLOW_ID" || -z "$PROPOSAL_ID" ]]; then
  echo "live_demo: IDs unvollständig" >&2
  exit 1
fi

echo "--- show ---"
pmi show "$REQUEST_ID" | head -c 2000
echo

# C2: eval optional
if [[ "${PMI_LIVE_EVAL:-}" == "1" ]]; then
  echo "--- eval justitia (optional) ---"
  pmi eval justitia "$REQUEST_ID" || true
fi

# L6: Auth vor approve = 0
AUTH_BEFORE="$(python3 - <<PY
import sqlite3
c=sqlite3.connect("$DB_PATH")
print(c.execute("SELECT COUNT(*) FROM Authorization").fetchone()[0])
c.close()
PY
)"
if [[ "$AUTH_BEFORE" != "0" ]]; then
  echo "live_demo: Authorization vor approve erwartet 0, ist $AUTH_BEFORE" >&2
  exit 1
fi

echo "--- approve ---"
pmi approve "$REQUEST_ID" "markus"
echo "--- run ---"
pmi run "$REQUEST_ID"

# Warten bis APPLIED
STATUS_OUT=""
for _ in $(seq 1 60); do
  STATUS_OUT="$(pmi status "$REQUEST_ID")"
  if echo "$STATUS_OUT" | grep -q "APPLIED"; then
    break
  fi
  sleep 2
done
echo "--- status ---"
echo "$STATUS_OUT"

if ! echo "$STATUS_OUT" | grep -q "APPLIED"; then
  echo "live_demo: Status nicht APPLIED" >&2
  exit 1
fi

STECKBRIEF="$REPO_PATH/projekte/$PROPOSAL_ID/steckbrief.md"
if [[ ! -f "$STECKBRIEF" ]]; then
  echo "live_demo: Steckbrief fehlt: $STECKBRIEF" >&2
  exit 1
fi
echo "Steckbrief: $STECKBRIEF"
ls -la "$REPO_PATH/projekte/$PROPOSAL_ID/"

# L5 proposed_by
PROPOSED_BY="$(python3 - <<PY
import sqlite3
c=sqlite3.connect("$DB_PATH")
print(c.execute("SELECT proposed_by FROM Proposal WHERE id=?", ("$PROPOSAL_ID",)).fetchone()[0])
c.close()
PY
)"
echo "proposed_by=$PROPOSED_BY"
if [[ "$PROPOSED_BY" != "agent:llm" ]]; then
  echo "live_demo: proposed_by nicht agent:llm" >&2
  exit 1
fi

# Kurzbericht-Datei (keine Secrets)
REPORT="$LIVE_DIR/live_report.txt"
{
  echo "request_id=$REQUEST_ID"
  echo "workflow_id=$WORKFLOW_ID"
  echo "proposal_id=$PROPOSAL_ID"
  echo "proposed_by=$PROPOSED_BY"
  echo "status_line=$(echo "$STATUS_OUT" | tr '\n' ' ')"
  echo "steckbrief=$STECKBRIEF"
  echo "model=${AGENT_MODEL:-claude-sonnet-5-5}"
  echo "temporal_host=$PMI_TEMPORAL_HOST"
  echo "fake_llm=off"
} >"$REPORT"
echo "Report: $REPORT"
echo "Live-E2E fertig."

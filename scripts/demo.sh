#!/usr/bin/env bash
# G5: FakeLLM-Durchstich init → start → show → approve → run → status → eval
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEMO_DIR="${PMI_DEMO_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/pmi-demo.XXXXXX")}"
DB_PATH="${DEMO_DIR}/control.db"
REPO_PATH="${DEMO_DIR}/repo"
mkdir -p "$REPO_PATH"
git -C "$REPO_PATH" init -q
git -C "$REPO_PATH" config user.email "demo@example.com"
git -C "$REPO_PATH" config user.name "PMI Demo"
git -C "$REPO_PATH" commit --allow-empty -qm "init"

export PMI_AUTHORIZATION_HMAC_SECRET="${PMI_AUTHORIZATION_HMAC_SECRET:-demo-secret}"
export PMI_DB_PATH="$DB_PATH"
export PMI_REPO_PATH="$REPO_PATH"
export PMI_TEMPORAL_HOST="${PMI_TEMPORAL_HOST:-localhost:7233}"
export PMI_TASK_QUEUE="${PMI_TASK_QUEUE:-pmi-initiation}"
export PMI_ALLOW_FAKE_LLM=1
export PMI_FAKE_LLM_JSON="${PMI_FAKE_LLM_JSON:-$(python3 - <<'PY'
import json
print(json.dumps({
  "steckbrief": {
    "titel": "Demo-Projekt",
    "ausgangslage": "Ist",
    "ziel": "Soll",
    "umfang": ["Modul A"],
    "nicht_umfang": [],
    "stakeholder": [{"name": "Ada", "rolle": "PL"}],
    "meilensteine": [],
    "budget_rahmen": None,
    "erfolgskriterien": ["K1"],
    "annahmen": [],
    "offene_fragen": [],
  },
  "risikoliste": {
    "risiken": [{
      "beschreibung": "Risiko",
      "ursache": "Ursache",
      "auswirkung": "Auswirkung",
      "wahrscheinlichkeit": "niedrig",
      "schadensausmass": "mittel",
      "massnahme": "Massnahme",
      "verantwortlich_vorschlag": "PL",
    }]
  },
}))
PY
)}"
export PYTHONPATH="${ROOT}:${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
export PMI_START_WAIT_SECONDS="${PMI_START_WAIT_SECONDS:-60}"

echo "Demo-Verzeichnis: $DEMO_DIR"
echo "Temporal: $PMI_TEMPORAL_HOST  Queue: $PMI_TASK_QUEUE"

pmi init

WORKER_PID=""
cleanup() {
  if [[ -n "${WORKER_PID}" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

pmi worker &
WORKER_PID=$!
sleep 2

START_OUT="$(pmi start "Demo-Aufgabe: FakeLLM-Durchstich")"
echo "$START_OUT"
REQUEST_ID="$(echo "$START_OUT" | sed -n 's/^request_id=//p')"
if [[ -z "$REQUEST_ID" ]]; then
  echo "demo: keine request_id von pmi start" >&2
  exit 1
fi

echo "--- show ---"
pmi show "$REQUEST_ID"
echo "--- approve ---"
pmi approve "$REQUEST_ID" "markus"
echo "--- run ---"
pmi run "$REQUEST_ID" || true
sleep 2
echo "--- status ---"
pmi status "$REQUEST_ID"
echo "--- eval justitia ---"
pmi eval justitia "$REQUEST_ID"

echo "Demo fertig (request_id=$REQUEST_ID)."

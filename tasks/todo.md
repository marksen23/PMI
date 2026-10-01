# Plan – Phase 1 Ausführungskern

Stand: 2026-10-01  
Grundlage: docs/Prototyp-Skizze-Phase1.md + Korrekturen K1–K7

---

## Haltepunkt 1: Dieser Plan – warte auf Freigabe

## Haltepunkt 2: Tests (rot) – warte auf Freigabe, bevor Implementierung beginnt

---

## Dateistruktur

```
config.py                  PROVISIONAL-Konfiguration
src/pmi/__init__.py
src/pmi/db.py              SQLite-Schema + DB-Zugriff
src/pmi/risk.py            Pure Risk-Funktion
src/pmi/git_adapter.py     Atomarer Git-Write (CAS, Idempotenz)
src/pmi/state_machine.py   Zustandsmaschine + Übergänge
src/pmi/agent.py           Agent-Stub (kein LLM)
src/pmi/approval.py        Approval-Service (serverseitiger Hash)
src/pmi/cli.py             CLI (approve + run)
tests/conftest.py          Fixtures: tmp-DB, tmp-git-Repo
tests/test_invariants.py   I1–I5 + CAS
tests/test_crash.py        C1–C4 (kill -9, Subprozess)
pyproject.toml             pytest + hypothesis
```

---

## Schritt 1 – config.py

Alle PROVISIONAL-Werte zentral, explizit markiert:

```python
# PROVISIONAL: RFC 8785 vs. sortierte Schlüssel → dokumentiert, kein RFC-8785-Paket nötig
CANONICAL_JSON_METHOD = "sorted_keys"  # PROVISIONAL

# PROVISIONAL: Authorization-Ablaufzeit
AUTHORIZATION_TTL_SECONDS = 86400  # PROVISIONAL: 24 h

# PROVISIONAL: Kosten-Kriterium deaktiviert
COST_CHECK_ENABLED = False  # PROVISIONAL

# PROVISIONAL: Maximale Proposal-Versionen
MAX_PROPOSAL_VERSIONS = None  # PROVISIONAL: unbegrenzt
```

Jede PROVISIONAL-Annahme wird im Abschlussbericht aufgelistet.

---

## Schritt 2 – src/pmi/db.py

SQLite-Schema (alle Tabellen aus Skizze Abschnitt 5):

```sql
Proposal         (id, version, proposed_by, payload_json, diff_json, created_at)
ExecutionRequest (id, proposal_id, proposal_version, request_hash, created_at,
                  consumed_by_execution_id)
Authorization    (id, request_id, decided_by, granted_at, expires_at, used)
Execution        (id, request_id, authorization_id, status, started_at,
                  zero_effect_proof, finished_at)
AuditEvent       (id, dedup_key, event_type, execution_id, seq, payload_json, created_at)
```

Hilfsfunktionen:
- `get_db(path) → Connection` (WAL-Modus, row_factory)
- `init_schema(conn)`
- `append_audit(conn, dedup_key, event_type, execution_id, seq, payload)` – Dedup via UNIQUE(dedup_key)

---

## Schritt 3 – src/pmi/risk.py

```python
def derive_risk(proposal: dict) -> str:
    # "PROPOSAL_ONLY" mit Außenwirkung → "UNGÜLTIG"
    # Kosten-Kriterium: Feld prüfen, aber COST_CHECK_ENABLED=False → ignoriert
    # Gibt "R0", "R1", "R2" oder "UNGÜLTIG" zurück
```

Reine Funktion, keine Seiteneffekte.

---

## Schritt 4 – src/pmi/git_adapter.py

`GitProjectStoreAdapter`:

```python
class GitProjectStoreAdapter:
    def apply(self, payload: dict, expected_revision: str) -> ApplyResult:
        # 1. CAS: HEAD == expected_revision? Sonst REVISION_CONFLICT
        # 2. Idempotenz: Commit-Nachricht enthält idempotency_key?
        #    → ALREADY_APPLIED, kein Doppel-Commit
        # 3. Temp-Datei schreiben
        # 4. Umbenennen (atomisch auf Linux)
        # 5. git add + git commit
        # Fault-Injection-Hooks für Tests (benannte Punkte: pre_commit, post_rename, post_commit)
```

Zustandsübergänge:
- `APPLIED` → erfolgreicher Commit
- `REVISION_CONFLICT` → CAS-Fehler
- `ALREADY_APPLIED` → eigener Idempotenz-Treffer
- `PARTIAL` → Temp vorhanden, kein Commit
- `DIVERGENT` → Store nach Commit manipuliert

---

## Schritt 5 – src/pmi/state_machine.py

Übergänge (jeder mit DB-Transaktion + AuditEvent):

```
PENDING → AUTHORIZED   approve()
AUTHORIZED → STARTED   begin_execution()  – prüft consumed_by_execution_id (K2)
STARTED → APPLIED      adapter.apply() erfolgreich
APPLIED → DIVERGENT    Store-Manipulation erkannt
DIVERGENT → ESCALATED  auto
STARTED → FAILED       nur mit ZERO_EFFECT_PROOF (I3)
```

Invarianten:
- Kein Adapter-Aufruf ohne STARTED in der DB (I2)
- `decided_by` ohne `human:`-Präfix → `ABGELEHNT` + AuditEvent(INVALID_DECIDED_BY) (I5)
- Hash-Bindung: payload nach Freigabe ändern → `HASH_MISMATCH` (I1)

---

## Schritt 6 – src/pmi/agent.py

Stub:
```python
def create_proposal(spec: dict) -> dict:
    # Gibt festes, schemakonformes Proposal zurück
    # Kein LLM-Aufruf
    # Status- und Entscheidungsfelder NICHT im Schema
    # Zusatzfelder → ValidationError
```

---

## Schritt 7 – src/pmi/approval.py

```python
def approve(db: Connection, request_id: str, person: str) -> Authorization:
    # Rendert Anzeige aus DB (nicht vom Aufrufer)
    # Berechnet Hash serverseitig (K5)
    # Nimmt weder Inhalt noch Hash vom Aufrufer entgegen
    # Schreibt Authorization mit decided_by=f"human:{person}"
```

---

## Schritt 8 – src/pmi/cli.py

```
pmi approve <request_id> <person>
pmi run     <request_id>
pmi status  <request_id>
```

---

## Schritt 9 – Tests (Haltepunkt 2)

### tests/test_invariants.py

| Test | Invariante | Methode |
|------|-----------|---------|
| test_hash_mismatch | I1: Payload-Änderung nach Freigabe | Direkt |
| test_auth_reuse | I1: Authorization zweimal verwenden | Direkt |
| test_auth_expired | I1: Abgelaufene Authorization | Direkt |
| test_no_mutation_without_started | I2: Adapter ohne STARTED | Direkt |
| test_audit_started_before_commit | I2: seq-Reihenfolge | DB-Inspektion |
| test_failed_requires_proof | I3: FAILED ohne ZERO_EFFECT_PROOF | Direkt |
| test_failed_never_with_changed_revision | I3: FAILED + veränderter Store | hypothesis |
| test_proposal_schema_no_status_fields | I4: Schema-Abstoßung | Direkt |
| test_injection_via_text | I4: "Status: freigegeben" ändert nichts | Direkt |
| test_decided_by_prefix | I5: human:-Präfix | Direkt + Audit-Check |
| test_cas_revision_conflict | CAS: Store nach Freigabe geändert | Direkt |

### tests/test_crash.py

Je Szenario: Subprozess mit `kill -9` an benanntem Fault-Injection-Punkt,
50 Läufe mit zufälligem Abbruchzeitpunkt.

| Test | Punkt | Prüfung |
|------|-------|---------|
| test_c1_before_begin_commit | pre_begin | nichts verbraucht, Store unverändert |
| test_c1b_after_begin_commit | post_begin | Wiederholung → eigener Verbrauch, genau 1 Schreibvorgang |
| test_c2_after_temp_before_rename | post_temp | PARTIAL → Bereinigung → Retry → genau 1 Revision |
| test_c2b_after_rename_before_commit | post_rename | PARTIAL erkannt, kein Doppel-Commit |
| test_c3_after_commit_before_ack | post_commit | ALREADY_APPLIED, kein Duplikat |
| test_c4_store_manipulated | post_commit_manip | DIVERGENT → ESCALATED |

Alle 6 × 50 = 300 Läufe: nie > 1 Revision, kein FAILED mit Effekt,
keine Lücke/kein Duplikat in der Hash-Kette.

---

## Schritt 10 – Implementierung (nach Haltepunkt 2)

Reihenfolge: db → risk → git_adapter → state_machine → agent → approval → cli

---

## Schritt 11 – Abschlussbericht

- Testausgabe (vollständig)
- Alle PROVISIONAL-Annahmen mit Wert und Begründung
- Jede Abweichung von Skizze/Korrekturen mit Begründung
- Offene Fragen (Abschnitt 12 der Skizze)
- Keine Entscheidung selbst treffen

---

## Status

- [x] Haltepunkt 1: Plan freigegeben
- [x] Projektgerüst aufgebaut (pyproject.toml, config.py, src/pmi/, tests/)
- [x] Haltepunkt 2: Tests (rot) freigegeben und geschrieben
- [x] Implementierung abgeschlossen (30 Tests, alle grün)
- [x] Alle Tests grün
- [x] Abschlussbericht erstellt

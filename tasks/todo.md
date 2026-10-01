# Plan – Sicherheitskern Phase 1 fertig

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

---

# Auftrag 1b – Kernkorrekturen (Haltepunkt 2: Tests rot)

Stand: 2026-10-01
Kern-Änderungen für die Punkte unten sind freigegeben. Implementierung erst nach
Freigabe dieser Tests. Bestehende Testdateien werden nicht geändert.

Die Reparatur im Activity-Wrapper ist verworfen. `begin_execution` und
`insert_request` erzwingen die Invarianten selbst.

## Tests in tests/test_core_1b.py

| Test | Soll vor der Implementierung | Zustand, den er liest |
|---|---|---|
| `test_foreign_authorization_does_not_consume_other_request` | rot | keine Execution, beide Requests unverbraucht, Authorization `used=0`, Commit-Anzahl gleich |
| `test_authorization_stores_approved_request_hash` | rot | Spalte `Authorization.request_hash` gleich dem Hash bei `approve`; nach Änderung der Request-Zeile keine Execution |
| `test_insert_request_same_inputs_returns_existing` | rot | gleiche ID, genau eine `ExecutionRequest`-Zeile |
| `test_begin_execution_retry_returns_same_execution` | rot | gleiche Execution-ID, eine Zeile, ein STARTED-Audit, `used=1` |
| `test_revision_conflict_fails_with_zero_effect_proof` | rot | `apply` liefert `REVISION_CONFLICT`, Status `FAILED`, `zero_effect_proof` gesetzt, kein weiterer Commit, Zieldatei fehlt |
| `test_insert_request_different_revision_creates_new` | grün | zwei Requests bei verschiedener `expected_revision` |
| `test_append_audit_dedup_keeps_one_row` | grün | eine Zeile, erste Sequenz und erstes Payload bleiben |

`transition(FAILED)` bei verändertem HEAD bleibt verboten. Das hält
`test_i3_hypothesis_no_failed_with_changed_revision` grün. Der CAS-Pfad setzt
`FAILED` nur, wenn kein Commit mit dem Idempotenzschlüssel existiert. Das ist
eine eigene Funktion in der Zustandsmaschine, kein gelockerter `transition`.

## Warum es keinen grünen C1b gab

Die Skizze nennt C1b: Crash nach dem Commit von `begin_execution`, Retry erkennt
den eigenen Verbrauch. Diesen Test gibt es nicht. `test_c0_crash_before_start_persist`
bricht vor dem Commit ab. Der Neustart legt einen neuen Request und eine neue
Authorization an und ruft `begin_execution` nicht für denselben Schlüssel auf.
`scenario_adapter_apply` liest eine vorhandene STARTED-Zeile und überspringt
`begin_execution`. `test_i1_auth_reuse_rejected` und `test_i7_auth_single_use`
erwarten beim zweiten Aufruf eine Exception und benutzen einen anderen Schlüssel.

## Implementierung nach Freigabe, noch nicht begonnen

1. `begin_execution`: `Authorization.request_id == request_id`. Gespeicherten
   `request_hash` gegen den aktuellen Request-Hash prüfen. Bei eigenem
   Idempotenzschlüssel die bestehende Execution zurückgeben, kein zweites STARTED.
   Anderer Schlüssel auf verbrauchter Authorization bleibt abgelehnt.
2. `approve` schreibt `request_hash` in `Authorization`. Spalte ergänzen, mit
   Default, damit vorhandene INSERT-Listen in den alten Tests weiterlaufen.
3. `insert_request`: bei gleichem `proposal_id`, `proposal_version`,
   `request_hash` und `expected_revision` die vorhandene ID zurückgeben.
4. `apply` bei CAS-Konflikt: Execution `FAILED` mit `zero_effect_proof`, kein Commit.
5. HMAC nicht ändern. Nur berichten.

---

# Auftrag 2 – Temporal-Einbindung (Plan, nicht gestartet)

Stand: 2026-10-01
Voraussetzung: Auftrag 1b grün und der Bericht dazu geprüft.
Auftrag 3 und 4 starten nicht. Auftrag 3: Anbieter/Modell und Datenschutzrahmen fehlen.
Auftrag 4: Projektauswahl, Raster, Zuordnungsregeln und Originale fehlen.

## Skizze

Im Repo beginnt `docs/Prototyp-Skizze-Phase1.md` bei Abschnitt 3. Abschnitt 11
schließt Temporal dort aus. Die Fassung mit Abschnitt 1 und 2 liegt hier nicht.
Hash-Vergleich erst, wenn diese Fassung als Datei im Repo liegt. Bis dahin kein
zweiter Spezifikationstext.

Der Kern aus Auftrag 1b ist Voraussetzung. Der Workflow ruft ihn auf und wiederholt
die Prüfungen nicht nebenher.

## Ablauf InitiationWorkflow

Workflow-Code deterministisch: kein SQLite, kein Git, kein `datetime.now`, kein `uuid`,
kein Lesen von Umgebungsvariablen. IDs und Uhrzeit für Wartefristen kommen aus
`workflow.info()` und `workflow.wait_condition`. Seiteneffekte nur in Activities.

Eingabe: `task`, `db_path`, `repo_path`, `approval_timeout_seconds`.
Der Timeout wird beim Start übergeben (im History festgehalten), nicht live aus `config.py` gelesen.

```
1. draft_proposal(task)
     Stub. Festes, schemakonformes Steckbrief-Payload, proposed_by="agent:stub".
     Schreibt Proposal über insert_proposal und ein Audit-Ereignis.
     offene_fragen ist leer. Kein LLM, kein Netz.
2. answers-Signal ist registriert.
     Warten nur, wenn offene_fragen nicht leer ist (im Stub-Lauf also nicht).
     Payload: list[str]. Keine Authorization, kein Status.
     Persistenz in diesem Auftrag: nur append_audit.
     Keine neue Proposal-Version (das wäre K4 und ein Kern-Thema).
3. create_request
     Activity liest git HEAD, ruft insert_request. Idempotent wie oben.
4. Warten auf Signal authorized(authorization_id: str) oder Timeout.
     Das Signal ist nur die ID. Kein decided_by, kein Hash, kein Payload.
5. Timeout zuerst → Request in der Kontroll-DB terminal, Audit EXPIRED, Ergebnis EXPIRED.
     Keine Execution, kein Commit. Eine danach erteilte Freigabe kann den Request
     nicht mehr ausführen. `pmi run` startet oder signalisiert im Temporal-Betrieb
     den Workflow und ruft den Kern nicht direkt auf.
6. Signal zuerst → begin_execution im Kern.
     Der Kern prüft Request-Bindung, gespeicherten Hash, HMAC, Verbrauch, Ablauf.
     Fremd oder verbraucht → keine Execution, kein Commit.
7. apply_write → adapter.apply.
     Idempotenzschlüssel ist der request_hash, nicht die Workflow-ID.
     REVISION_CONFLICT setzt im Kern FAILED mit zero_effect_proof.
     Keine neue Freigabe im selben Workflow (K1).
8. verify_write: nur lesen. Bundle-Hash bzw. Zieldatei gegen den freigegebenen Stand.
     Bei Abweichung reconcile.
9. reconcile → reconcile_applied_executions.
10. Ergebnis = Status, den eine Activity aus der Kontroll-DB liest.
```

EXPIRED ist das Ablaufen der Wartefrist. Der Request wird dabei in der DB terminal.
SQLite `datetime('now')` bewegt sich beim Zeitsprung nicht. Eine schon erteilte
Freigabe prüft der Kern weiter über die DB. Der Zeitsprung-Test prüft die Wartefrist
und den terminalen Request.

## Activities, Timeouts, Retries

Alle Werte PROVISIONAL, zur Freigabe mit diesem Plan.
Geschäftsfehler (fremde oder verbrauchte Authorization, HASH_MISMATCH, REVISION_CONFLICT,
abgelaufene Authorization) sind non-retryable. Prozessabbruch ist retryable.
Backoff jeweils Faktor 2, Startintervall 1 s.

| Activity | start_to_close | maximum_attempts | Begründung |
|---|---|---|---|
| draft_proposal | 30 s | 5 | Lokaler Stub, kein Netz. Versuche nur für Prozessabbruch. |
| create_request | 10 s | 5 | Ein SQLite-Insert, idempotent per Lookup. |
| begin_execution | 10 s | 5 | Eine DB-Transaktion. Retry darf keine zweite Execution erzeugen. |
| apply_write | 60 s | 5 | git add/commit. Retry erkennt den eigenen Commit. |
| verify_write | 30 s | 5 | Nur Lesen von git und Dateien. |
| reconcile | 30 s | 5 | Statusübergang, idempotent über die Zustandsmaschine. |
| append_audit | 10 s | 5 | Dedup-Schlüssel, Insert oder No-op. |

Dedup-Schlüssel: `{workflow_id}:{step}:{event_type}`. `step` ist eine feste
Schrittnummer des Workflows. Nur die Wiederholung desselben Schritts fällt zusammen.
Ein zweites `answers` oder ein zweiter Übergang bekommt eine andere Schrittnummer.

Produktions-Timeouts stehen in der Tabelle. Tests setzen kürzere `start_to_close`-
und Heartbeat-Fristen über die Activity-Optionen beim Start, ohne diese Tabelle
zu ändern. Sonst erkennt Temporal einen SIGKILL erst nach 10–60 s, und 50 Läufe
je Activity dauern Stunden.

Erschöpfte Retries von `apply_write` enden mit `reconcile`. Kein Workflow endet mit
einer offenen Execution. `RECOVERY_REQUIRED` gibt es in der Zustandsmaschine nicht
und wird nicht erfunden. Der Endzustand ist der, den der Kern schreibt (`FAILED`
mit Nachweis oder `ESCALATED`). Dafür gibt es einen eigenen Test.

## Dateien (nach Freigabe der Tests)

```
src/pmi/temporal/workflow.py     InitiationWorkflow
src/pmi/temporal/activities.py   Wrapper um den bestehenden Kern, Fault-Hook
src/pmi/temporal/worker.py       Worker-Start
tests/test_temporal.py           Neue Tests
pyproject.toml                   Abhängigkeit temporalio (Version beim Implementieren pinnen)
```

Abhängigkeit: Python-SDK `temporalio`. Zeitsprung über `WorkflowEnvironment.start_time_skipping()`.
Kill-Tests über `WorkflowEnvironment.start_local()` und einen Worker-Subprozess.
Kein Eingriff in den Kern, um den Dev-Server zu starten.

## Tests (Haltepunkt 2, noch nicht geschrieben)

Bestehende Tests unter `tests/` bleiben unverändert und laufen in derselben Suite.

Neue Tests, echte SQLite-DB und echtes git-Repo je Lauf, kein Mock des Kerns:

| Test | Prüfung am Zustand, nicht am Rückgabewert allein |
|---|---|
| `test_wait_timeout_expired` | Zeitsprung über die Wartefrist, kein Signal. Keine Execution, Commit-Anzahl unverändert, Request terminal, Audit `EXPIRED`, Workflow-Ergebnis `EXPIRED`. Danach erteilte Authorization führt über die CLI nicht am Workflow vorbei. |
| `test_apply_retries_exhausted_reconciles` | Fünf Fehlschläge von `apply_write`, danach reconcile. Execution nicht mehr offen. Status in der DB ist `FAILED` oder `ESCALATED`. Workflow-Ergebnis gleich DB. |
| `test_foreign_authorization_rejected` | Signal trägt die ID einer Authorization eines anderen Requests. Keine Execution für diesen Request, dessen Authorization unbenutzt, Commit-Anzahl unverändert. |
| `test_consumed_authorization_rejected` | Signal trägt eine bereits verbrauchte Authorization-ID. Kein zweiter Commit, keine zweite Execution. |
| `test_sigkill_*` | Je Activity 50 Läufe, gleicher Fault-Point. Worker-Prozess endet durch `SIGKILL` (`os.kill(pid, SIGKILL)`), keine geworfene Exception. Danach sauberer Neustart des Workers. |
| `test_status_matches_control_db` | Nach jedem Lauf Workflow-Ergebnis gegen die Kontroll-DB. Abweichung → Test rot. Die DB wird nicht dem Workflow angeglichen. |

Sigkill-Fälle und der Zustand danach:

| Lauf | Fault | Zustand nach Retry |
|---|---|---|
| draft_proposal, create_request, verify_write, append_audit | nach der Kernwirkung, vor Activity-Return | genau eine Zeile bzw. ein Audit, Retry erzeugt keine zweite |
| begin_execution vor Persist | wie C1 | nichts verbraucht, Store unverändert, Retry genau eine Execution |
| begin_execution nach Persist | wie C1b | Retry erkennt den eigenen Verbrauch, genau eine Execution |
| apply_write vor Commit | wie C2/C4a | Bereinigung, genau ein Commit |
| apply_write nach Commit | wie C3 | `ALREADY_APPLIED`, kein zweiter Commit |
| reconcile | nach dem Übergang, vor Return | ein Übergang, Status `DIVERGENT`/`ESCALATED` nur wenn der Store abweicht |
| Manipulation nach Apply | wie C4, kein Kill | reconcile setzt `ESCALATED`, Workflow-Ergebnis gleich DB |

Zusätzlich in jedem dieser Läufe: keine doppelten `dedup_key` in `AuditEvent`,
nie mehr als eine neue Revision, kein `FAILED` ohne `zero_effect_proof`.

`maybe_crash` im Kern bleibt `os._exit(1)`. Der Sigkill-Hook liegt nur in
`activities.py` und ist nur aktiv, wenn `PMI_FAULT_INJECT` gesetzt ist.

## Checkliste Haltepunkt 2 (vor Implementierung)

Prüfung, ob die Tests scheitern können. Stil ist kein Kriterium.

- Gegen den echten Zustand: Commit-Anzahl, Authorization (`used`, Ablauf, Request-Bindung),
  Execution-Status, Audit-Kette (`dedup_key`, keine Lücke, kein Duplikat).
  Ein Test, der nur `APPLIED` vom Workflow entgegennimmt, gilt als nicht bestanden.
- Negativfall je Invariante, die der Workflow berührt: fremde Authorization, verbrauchte
  Authorization, abgelaufene Wartefrist, Replay nach Abbruch, Store-Manipulation.
- K1–K7 bleiben durch die bestehenden Tests abgedeckt. Die neuen Tests dürfen diese
  Fälle nicht aus der Suite nehmen. K3 zusätzlich: gefälschtes Signal wird nur über
  die DB-Activity bewertet.
- C1–C4 und die neuen Kill-Läufe beenden einen Prozess (`SIGKILL` bzw. bestehendes
  `os._exit`), sie werfen keine Exception als Ersatz.
- Invarianten- und Absturztests: echte SQLite-DB, echtes git-Repo. Kein Mock von
  Adapter, Zustandsmaschine oder Approval.
- Gegenprobe an einer Kopie, nicht am Arbeitsbaum: CAS-Frühreturn in `apply` entfernen.
  Mindestens ein Test muss rot werden. Wird keiner rot, werden die Tests nicht zur
  Implementierung vorgelegt. Die Kopie wird verworfen, nicht committet.
  Neue Workflow-Tests, die vor der Implementierung nur wegen fehlendem Code rot sind,
  beweisen damit noch nicht, dass sie CAS erkennen. Dieselbe Gegenprobe läuft nach
  dem ersten grünen Lauf erneut, vor dem Bericht.

## Versionierung von Workflow-Code (Antwort im späteren Bericht)

Vorschlag, mit diesem Plan freizugeben: brechende Änderungen am Workflow bekommen
einen neuen Typnamen `InitiationWorkflow-vN`. Der Worker registriert den alten Typ,
bis keine offenen Läufe dieses Typs mehr existieren. `workflow.patched` nur für
Änderungen, die alte History weiter abspielen können. Laufende Executions werden
nicht auf neuen Code umgehängt.

## Übernommene Korrekturen am Plan

1. Vollständige Skizze fehlt im Repo. Hash-Vergleich erst mit der Datei.
2. EXPIRED über die Wartefrist, und der Request wird in der DB terminal.
3. Request-Bindung und K2 liegen in `begin_execution` und `insert_request`, Auftrag 1b.
4. `answers` nur als Audit, für den Stub.
5. Timeout-Tabelle gilt für Produktion. Tests überschreiben die Fristen.
6. `pmi run` führt im Temporal-Betrieb nicht direkt aus.
7. Dedup-Schlüssel enthält die Schrittnummer.
8. Idempotenzschlüssel ist der `request_hash`.
9. REVISION_CONFLICT und erschöpfte `apply_write`-Retries hinterlassen keine offene Execution.
10. `RECOVERY_REQUIRED` ist kein Zustand im Code und wird nicht ergänzt.

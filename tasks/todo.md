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

# Auftrag 1b – Kernkorrekturen

Stand: 2026-10-01
Tests lagen rot vor. Umsetzung im Kern, nicht im Wrapper. Bestehende Testdateien
außer `tests/test_core_1b.py` sind unverändert. In dem neuen Test steht nach der
Hash-Änderung ein `commit()`, sonst bleibt eine offene Transaktion und jede
Exception täuscht einen Treffer vor.

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

## Implementierung

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
Stand 2026-10-02: diese vier Eingaben liegen vor, der Auftrag ist erteilt.
Der Plan steht unten. Der Live-Lauf gegen das PDF wartet auf den DPA.

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

---

# Auftrag 4 – Prüfer für die Kurzfassung Justitia 4.0

Stand: 2026-10-02
Auftrag erteilt. Haltepunkt 1 ist freigegeben, einschliesslich der beiden Präzisierungen
unten. Tests entstehen vor dem Prüfer.

## Was der Auftrag ist

Ein Prüfer für ein schon vorhandenes `ProposalOutput`. Er setzt L6 um.
Er ruft kein Modell auf, liest keinen API-Schlüssel und schickt das PDF nicht.
Schema, Provider, Workflow und `config.py` bleiben unverändert.

Der Live-Lauf der Kurzfassung bleibt gesperrt, bis ein Auftragsverarbeitungsvertrag
vorliegt. Diesen Lauf enthält der Auftrag nicht.

## Quelle

Nur der Wortlaut der Kurzfassung `j40-auftrag-kurz-v2.0-20190522`.
Die acht Sätze aus Kapitel 7, als Referenz, nicht als erfundenes Projekt:

1. Projektorganisation & Governance zu komplex/handlungsunfähig
2. Widerstand der Benutzer
3. Widerstand der kommerziellen Anbieter
4. ungenügende Ressourcen (personell und finanziell); keine genügende Finanzierung resp. Finanzierungsstopp
5. Unterschätzung der Tragweite und der Komplexität
6. Rechtsetzungsprozess verzögert sich
7. Verzögerung beim Beschaffungsprozess (WTO)
8. Projektumfang nicht genügend klar festgelegt respektive interpretierbar.

Titel, Ziel, Umfang, Stakeholder und die übrigen Steckbrief-Felder bewertet der Prüfer
nicht. Dafür gibt L6 keine Pass/Fail-Regel. Das bleibt beim Menschen.

## Drei Noten

1. Hart, Pass oder Fail. `meilensteine` muss `[]` sein. `budget_rahmen` muss `None` sein.
   Jeder andere Wert ist Fail, auch 18,5 Millionen, 40 Millionen oder 89 Millionen.
2. Belegtreue. Jede `beschreibung` wird den acht Sätzen zugeordnet.
   Kein Treffer: unbelegt, Note Fail für diesen Eintrag.
   Ein Satz fehlt oder kommt doppelt vor: Qualitätsvermerk, kein Fail.
3. Ergänzung. `wahrscheinlichkeit` und `schadensausmass` werden nur als ergänzt markiert.
   In `ursache`, `auswirkung`, `massnahme` und `verantwortlich_vorschlag` markiert der Prüfer
   Ziffern und die Floskeln „regelmässig prüfen“, „regelmäßig prüfen“ und „Projektleitung“
   als schwach. Rollen darüber hinaus bewertet er nicht.

## Entschieden mit der Freigabe

Zuordnung: `whitespace_collapse` auf beiden Seiten, dann Kleinschreibung.
Treffer, wenn der Satz in der Beschreibung steht oder die Beschreibung im Satz.
Eine leere Beschreibung nach dem Zusammenziehen trifft keinen Satz, sonst wäre
`"" in satz` für jeden Satz wahr. Umschreibungen ohne diese Deckung bleiben beim
Menschen und gelten nicht automatisch als unbelegt.

Die acht Sätze leben in `src/pmi/justitia_reference.py` als `KAPITEL7_SAETZE`.
Prüfer und Tests importieren diese Liste. Keine zweite Abschrift.

Ziffern: jede Ziffernfolge in den vier Textfeldern. Floskeln: nur „regelmässig prüfen“,
„regelmäßig prüfen“ und „Projektleitung“, kleingeschrieben.

## Tests nach Freigabe, vorher nicht geschrieben

Echte Prüfung der Note, kein Mock des Schemas. Kein Netz.

| Test | Soll |
|---|---|
| leere Meilensteine und `budget_rahmen is None` | Hart: Pass |
| ein Meilenstein | Hart: Fail |
| Budget gesetzt, auch als Nutzenzahl | Hart: Fail |
| Beschreibung gleich Satz 1 | belegt |
| derselbe Satz kleingeschrieben, oder ein Teilsatz daraus | belegt |
| leere Beschreibung | unbelegt |
| Beschreibung, die keinen der acht Sätze enthält | unbelegt |
| zwei Einträge zu Satz 1, Satz 2 fehlt | Qualitätsvermerk, kein Fail der Belegtreue |
| „regelmässig prüfen“ in der Massnahme | schwach |
| Ziffer in der Ursache | schwach |
| gesetzte Wahrscheinlichkeit | nur ergänzt, Hart bleibt Pass |

## Dateien

`src/pmi/justitia_reference.py`, `src/pmi/eval_justitia.py`, `tests/test_eval_justitia.py`.
Kein zweites Spezifikationsdokument.

## Live-Lauf (2026-10-02)

Freigabe für 16384 liegt vor. Der Provider sendet diesen Wert. Der Lauf endete
mit `end_turn`. `evaluate()` hat hart nicht bestanden: Meilensteine befüllt,
`budget_rahmen` mit den Nutzenzahlen aus Kapitel 5. Drei von fünfzehn
Beschreibungen treffen einen Kapitelsatz. Keine Authorization.

## IHP-Rubrik (2026-10-02)

Freigabe liegt vor. Wortlaut in L8, TED 274205-2025. Prüfer und Tests sind
nicht beauftragt.

## IHP-Live-Lauf (2026-10-02, fortgesetzt)

Freigabe, `ANTHROPIC_API_KEY` und `ANTHROPIC_WORKSPACE_ID` (nur Umgebung).
Quelle TED 274205-2025. Lauf: `claude-sonnet-5-5`, `max_tokens` 16384,
`stop_reason=end_turn`. Gültiges Proposal, Authorization=0. Hart nach L8
bestanden; unbelegte Zusatzdaten in Meilensteinen. Artifact:
`/opt/cursor/artifacts/ihp-live-eval.json`. Prüfer nicht beauftragt.
`ClaudeLLMProvider` kennt den Workspace-Header noch nicht.

---

# Auftrag 5 – Prüfer TED 274205-2025 (IHP DMS)

Stand: 2026-10-02. Erteilt, umgesetzt, grün.

## Entscheidungen (A–D)

- **A** Zahlenformat: normalisiert. Punkt als Tausender, Komma als Dezimal.
  „317.829,00 EUR" und „317829.00" ergeben beide 317829.0. Mehrere Beträge
  im Fliesstext werden einzeln erkannt (Live-Lauf-Befund L8).
- **B** Laufzeitdaten: überall im serialisierten `ProposalOutput` gesucht
  (`model_dump_json()`), nicht auf `meilensteine[].zieltermin` eingeschränkt.
- **C** Modulnamen `eval_ihp.py` / `ihp_reference.py` / `test_eval_ihp.py`.
- **D** Veraltete L8-Zeile in PR #5 vor dem Merge korrigiert (`2236deb`).

## Noten (L8)

1. Hart: Zuschlag in `budget_rahmen`; 858074 nur mit „höchst…"; Meilensteine
   nicht leer; beide Laufzeitdaten irgendwo im Proposal.
2. Unbelegt: jedes weitere `meilensteine[].zieltermin` ausser Start/Ende.
3. Schwach: Ziffern oder Floskeln in den vier Risikotextfeldern.

## Dateien

- `src/pmi/ihp_reference.py`
- `src/pmi/eval_ihp.py`
- `tests/test_eval_ihp.py`

## Status

- [x] PR #5 gemergt (L8 auf main)
- [x] Tests inkl. Live-Budgettext und Unbelegt
- [x] Implementierung grün (`tests/test_eval_ihp.py` + Justitia unverändert)

---

# Auftrag 6 – Prüfer ND HHS Bed Management Closeout

Stand: 2026-10-03  
Status: **Umgesetzt, grün (Haltepunkt 3)**

Quelle: Closeout-Report 27.5166.02000, sha256
`9cdb90d5cc07947a3f995bba39dc17a973b610823ce2d4780859b09046999747`.
PDF lokal unter `it-projekte/` (gitignored).

## Entscheidungen A–F (human:markus, 2026-10-03)

- **A** Adapter ohne Schemaänderung: KEY-Zeilen in
  `budget_rahmen`, `meilensteine`, `erfolgskriterien`, `annahmen`,
  `ausgangslage`, `risikoliste[i].beschreibung`; Konflikt = HART-Fail;
  Zahlen ohne KEY nie zu fakten.
- **B** fehlender Key = nicht bewertet (T18 Pass).
- **C** DE-Format wenn eindeutig (`^\d{1,3}(\.\d{3})*,\d{2}$`), sonst SCHWACH.
- **D** nur Quellwerte, keine Nachrechnung als HART.
- **E** L6/L8 nur Muster; Raster gilt für ND.
- **F** Apostroph ’→' vor ZUORDNUNG.
- Report-Pfade = Herkunft (`steckbrief.…`), nicht `fakten[…]`.

## Dateien

- `src/pmi/nd_bed_management_reference.py` – REFERENZLISTE / Sätze
- `src/pmi/nd_bed_management_adapter.py` – KEY-Zeilen → fakten/aussagen
- `src/pmi/eval_nd_bed_management.py` – `evaluate() → NdBedEval`
- `tests/test_eval_nd_bed_management.py` – T01–T24

## Haltepunkte

1. [x] Plan inkl. A–F freigegeben.
2. [x] Rote Tests T01–T24 freigegeben.
3. [x] Implementierung grün: 25 passed in `test_eval_nd_bed_management.py`
   (gesamt mit Justitia/IHP: 65 passed).

## Nicht in diesem Auftrag

- Live-Lauf / Modellaufruf
- Schemaänderung an ProposalOutput
- Authorization/Status/decided_by
- Varianz-Nachrechnung als HART

---

# Auftrag 7 – Agentenarchitektur schliessen (Plan)

Stand: 2026-10-03  
Status: **Haltepunkt 2 – rote Tests vorgelegt, warte auf Freigabe**  
Ziel: KI-Plattform für Projektmanagement – Agentenpfad von Aufgabe bis
genehmigtem Steckbrief im Store, ohne UI und ohne Deploy.

## Entscheidungen A–F (freigegeben 2026-10-03)

| ID | Wahl | Kurz |
|---|---|---|
| **A** | **A2** | Schritt 2 weglassen; `offene_fragen` nur im Steckbrief; K4 bleibt zu |
| **B** | **B1** | alle TextBlocks konkatenieren; Thinking/andere ignorieren |
| **C** | **C1** | kein Evaluator im Workflow |
| **D** | **D1** | `start_to_close=120s`, `maximum_attempts=3`; PROVISIONAL in config.py |
| **E** | **E1** | Temporal-Tests mit `FakeLLMProvider`; Live-Key ausserhalb |
| **F** | **F1** | nur Agentenarchitektur; kein UI/Deploy/LangGraph |

## Ist-Stand (Inventur)

| Baustein | Stand |
|---|---|
| Sicherheitskern (DB, Approval, Zustandsmaschine, CAS-Git) | grün |
| `run_agent` + `ProposalOutput` + `FakeLLMProvider` | vorhanden |
| `ClaudeLLMProvider` (max_tokens 16384, Workspace-Header) | vorhanden |
| `GitProjectStoreAdapter._apply_steckbrief` | vorhanden (`steckbrief` im Payload) |
| Prüfer Justitia / IHP / ND Bed | grün, ausserhalb Temporal |
| Temporal `InitiationWorkflow` | läuft; Schritt 2 (`answers`) fehlt |
| Activity `draft_proposal` | **Stub** (`action/path/scope`, `proposed_by=agent:stub`) |
| Provider `content[0].text` | bricht bei ThinkingBlock |

Lücke: Temporal-Draft erzeugt kein LLM-`ProposalOutput`. Der Store-Pfad für
Steckbriefe existiert schon; der Workflow nutzt ihn nicht.

## Soll-Ablauf (freigegeben)

```
task
  → draft_proposal: run_agent(Claude|Fake) → Proposal + Audit AGENT_PROPOSAL_GENERATED
  → create_request (Payload = ProposalOutput, Hash gebunden)   # A2: kein answers
  → wait authorized | EXPIRED
  → begin_execution (Kern prüft HMAC/Bindung/Verbrauch)
  → apply_write → _apply_steckbrief → projekte/<id>/ im git-Store
  → verify_write / reconcile
  → Status aus Kontroll-DB
```

Invarianten bleiben: keine Authorization/Status aus LLM; `decided_by` nur
`human:`; echte SQLite + echtes git in Invarianten-/Absturztests.

## Dateien (nach Freigabe der Tests)

```
src/pmi/claude_provider.py          ThinkingBlock-Fix (B1)
src/pmi/temporal/activities.py      draft_proposal → run_agent; get_llm_provider()
src/pmi/temporal/workflow.py        Timeout D1 (120s / 3)
tests/test_claude_provider.py       TextBlock-Konkatenation (B1)
tests/test_temporal_agent.py        FakeLLM → Steckbrief-Apply, Invarianten
config.py                           DRAFT_PROPOSAL_* PROVISIONAL (D1)
```

Bestehende Temporal-Tests (touch_file-Stub) müssen bei Implementierung über
`get_llm_provider()` → FakeLLM weitergrün bleiben bzw. Steckbrief-Pfad nutzen.
Kein neues Spezifikationsdokument. Kurzer `docs/decisions.md`-Eintrag nur bei
neuer Abweichung.

## Tests (Haltepunkt 2)

Rote Tests vorgelegt. Echte DB + git; Kern nicht mocken. Kein `answers_*` (A2).

| Test | Soll |
|---|---|
| `test_draft_uses_run_agent_payload` | Proposal = `ProposalOutput`; `proposed_by=agent:llm`; kein `touch_file` |
| `test_draft_dedup_returns_same_proposal` | Retry derselben step/dedup → dieselbe proposal_id; ein AGENT_PROPOSAL_GENERATED |
| `test_apply_steckbrief_after_approve` | FakeLLM-Draft → approve → apply → `projekte/<id>/`; Status APPLIED |
| `test_llm_creates_no_authorization` | nach Draft: Authorization-Count 0 |
| `test_provider_concatenates_text_blocks` | Thinking + zwei TextBlocks → konkatenierter Text (B1) |
| `test_draft_timeout_constants_provisional` | config: 120s / attempts 3 (D1) |

## Haltepunkte

1. [x] Plan inkl. A–F freigegeben (A2 B1 C1 D1 E1 F1).
2. [ ] Rote Tests – vorgelegt, **warte auf Freigabe**.
3. [ ] Implementierung bis grün; Testausgabe im Bericht.

## Nicht in diesem Auftrag (F1)

- UI / Frontend
- Deploy / Hosting
- LangGraph, Policy-Engine
- Schemaänderung an `ProposalOutput`
- Evaluator im Workflow (C1)
- answers / K4 (A2)
- Live-Lauf gegen PDF (nur wenn Mark extra freigibt)
- Reconciliation-Strategie `DIVERGENT` (Abschnitt 12, PROVISIONAL `none`)

# Prototyp Phase 1 – Ausführungskern

Aufgabe: Ausführungskern des Prototyps Phase 1 – ohne Temporal, ohne LangGraph, ohne Policy-Engine.
Inhaltliche Grundlage: Abschnitte 3–7 dieses Dokuments.
Temporal und LangGraph kommen später. Baue den Kern so, dass jede Operation später
unverändert als Temporal-Activity laufen kann (at-least-once, also durchgängig idempotent).

---

## Abschnitt 3 – GitProjectStoreAdapter

Der `GitProjectStoreAdapter` ist die einzige Schreibschnittstelle zum Projekt-Repository.

- Jeder Schreibvorgang läuft als atomarer Commit (Temp-Datei → Umbenennen → Commit).
- `adapter.apply(payload, expected_revision)` führt ein Compare-and-Swap gegen den aktuellen HEAD durch.
  Abweichung → `REVISION_CONFLICT`, kein Schreibvorgang.
- Der Adapter erkennt seinen eigenen vorherigen Schreibvorgang anhand der Commit-Nachricht
  (Idempotenzschlüssel) und meldet Erfolg ohne Doppel-Commit.
- Partial-Zustand: Temp-Datei vorhanden, aber kein Commit → `PARTIAL` → Bereinigung → Retry.
- Nach erfolgreichem Commit aber vor Rückmeldung abgestürzt → `ALREADY_APPLIED`, kein Duplikat.
- Store-Datei nach Commit manipuliert → `DIVERGENT` → `ESCALATED`.

## Abschnitt 4 – Risikoableitung und Policy

### 4.1 Risikoableitung (reine Funktion)

```
derive_risk(proposal) → RiskLevel
```

- Eingabe: Proposal-Payload (schema-validiert).
- Ausgabe: `R0` (kein Risiko), `R1` (niedriges Risiko), `R2` (menschliche Freigabe erforderlich).
- `PROPOSAL_ONLY` mit Außenwirkung ist `UNGÜLTIG`, nicht `R1`.
- Kosten als zusätzliches Kriterium vorsehen (Feld vorhanden, im Prototyp immer `false`).
- Keine Seiteneffekte, keine DB-Zugriffe.

### 4.2 Policy (reine Python-Funktion)

- `R2` verlangt genau eine menschliche Freigabe vor der Ausführung.
- `decided_by` muss Präfix `human:` tragen; alle anderen Werte werden abgewiesen und als
  Audit-Ereignis protokolliert.
- Keine LLM-Ausgabe darf auf einem Codepfad eine Authorization erzeugen oder einen Status setzen.

## Abschnitt 5 – Kontroll-DB (SQLite)

Tabellen:

| Tabelle | Zweck |
|---------|-------|
| `Proposal` | Versionierte Vorschläge (proposed_by ∈ {agent:*, human:*, mixed}) |
| `ExecutionRequest` | Gebundene, gehashte Ausführungsanforderung |
| `Authorization` | Menschliche Freigabe (einmalig verwendbar, ablaufend) |
| `Execution` | Ausführungsprotokoll |
| `AuditEvent` | Unveränderliches Ereignisprotokoll |

Details:

- `request_hash`: SHA-256 über kanonisches JSON (RFC 8785 oder sortierte Schlüssel, Methode dokumentiert).
- Proposals sind versioniert. Eine menschliche Bearbeitung erzeugt eine neue Proposal-Version
  und damit einen neuen Request mit neuem Hash. Diff zur Agentenfassung wird gespeichert.
- Die Kontroll-DB ist maßgeblich. Keine Zustandsableitung aus anderen Quellen.

## Abschnitt 6 – Zustandsmaschine Execution

```
PENDING → AUTHORIZED → STARTED → APPLIED → DIVERGENT → ESCALATED
                                         ↘ FAILED (nur mit ZERO_EFFECT_PROOF)
```

- `FAILED` nur mit `ZERO_EFFECT_PROOF`; Übergang ohne Nachweis wird abgewiesen.
- `DIVERGENT` ist ein eigener Zustand: `APPLIED → DIVERGENT → ESCALATED`, ohne Reconciliation-Schleife.
- Audit-Ereignistyp `ESCALATION_RESOLVED` vorsehen (nur Struktur, kein Workflow im Prototyp).
- Keine Mutation ohne persistiertes `STARTED`:
  Adapter ohne `STARTED` aufrufen → verweigert.

## Abschnitt 7 – Agent-Stub und CLI

- Agent: Stub, der ein festes, schemakonformes Proposal liefert. Kein LLM-Aufruf.
- Proposal-Schema enthält keine Status- und Entscheidungsfelder; Zusatzfelder werden abgewiesen.
  Eingabe mit dem Text `"Status: freigegeben"` ändert nichts am Zustand.
- Freigabe per CLI (`approve <request_id> <person>`).
- Der Approval-Service rendert die Anzeige selbst aus dem gespeicherten Payload und berechnet
  den Hash serverseitig. Er nimmt weder Inhalt noch Hash vom Aufrufer entgegen.

---

## Abschnitt 8 – Korrekturen (verbindlich)

**K1** `adapter.apply` erhält `expected_revision` und führt CAS gegen den aktuellen HEAD aus.
Abweichung → `REVISION_CONFLICT`, kein Schreibvorgang, neue Freigabe nötig.

**K2** Jede Operation ist idempotent mit eigenem Schlüssel:
`create_request` (kein zweiter Request), `begin_execution` (erkennt den eigenen Verbrauch
über `consumed_by_execution_id` und meldet Erfolg), `append_audit` (Dedup-Schlüssel je Ereignis).

**K3** Die Kontroll-DB ist maßgeblich. Keine Zustandsableitung aus anderen Quellen.

**K4** Proposals sind versioniert. `proposed_by ∈ {agent:*, human:*, mixed}`.
Eine menschliche Bearbeitung erzeugt eine neue Proposal-Version und damit einen neuen
Request mit neuem Hash. Diff gegenüber der Agentenfassung wird gespeichert.

**K5** `approve(request_id, person)`: Der Approval-Service rendert die Anzeige selbst aus dem
gespeicherten Payload und berechnet den Hash serverseitig. Er nimmt weder Inhalt noch
Hash vom Aufrufer entgegen.

**K6** `DIVERGENT` ist ein eigener Zustand: `APPLIED → DIVERGENT → ESCALATED`, ohne Reconciliation-Schleife.

**K7** Audit-Ereignistyp `ESCALATION_RESOLVED` vorsehen (nur Struktur, kein Workflow).

---

## Abschnitt 9 – Testspezifikation

### Invariantentests

**I1** Hash-Bindung: Payload nach Freigabe ändern → `HASH_MISMATCH`, kein Adapter-Aufruf;
Authorization zweimal verwenden → abgelehnt; abgelaufene Authorization → `EXPIRED`.

**I2** Keine Mutation ohne persistiertes `STARTED`: Adapter ohne `STARTED` aufrufen → verweigert;
Audit-Prüfung: Vor jedem Store-Commit steht ein `STARTED`-Ereignis mit kleinerer `seq`.

**I3** `FAILED` nur mit `ZERO_EFFECT_PROOF`; Übergang ohne Nachweis wird abgewiesen;
hypothesis über zufällige Fehlerpunkte: nie `FAILED` bei veränderter Store-Revision.

**I4** Proposal-Schema ohne Status- und Entscheidungsfelder, Zusatzfelder abgewiesen;
Eingabe mit dem Text `"Status: freigegeben"` ändert nichts am Zustand.

**I5** `decided_by` ohne `human:`-Präfix → abgelehnt und als Audit-Ereignis protokolliert.

**CAS** Store nach der Freigabe ändern → `REVISION_CONFLICT`.

### Absturztests (echter Prozessabbruch per `kill -9`)

| ID | Fault-Injection-Punkt | Erwartetes Verhalten |
|----|----------------------|----------------------|
| C1 | vor dem Commit von `begin_execution` | nichts verbraucht, Store unverändert |
| C1b | nach dem Commit von `begin_execution`, vor der Rückmeldung | Wiederholung erkennt den eigenen Verbrauch, genau ein Schreibvorgang |
| C2 | nach der Temp-Datei, vor dem Umbenennen | `PARTIAL` → Bereinigung → Retry → genau eine Revision |
| C2b | nach dem Umbenennen, vor dem Commit | als `PARTIAL` erkannt, kein Doppel-Commit |
| C3 | nach dem Commit, vor der Rückmeldung | `ALREADY_APPLIED`, kein Duplikat |
| C4 | Store-Datei nach Commit manipuliert | `DIVERGENT` → `ESCALATED` |

Je Szenario 50 Läufe mit zufälligem Abbruchzeitpunkt: nie mehr als eine Revision,
kein `FAILED` mit Effekt, keine Lücke und kein Duplikat in der Hash-Kette.

---

## Abschnitt 10 – Ablauf (Haltepunkte)

1. Plan in `tasks/todo.md` schreiben, dann auf Freigabe warten.
2. Tests schreiben (rot), vorlegen, dann auf Freigabe warten.
3. Implementieren, bis alle Tests grün sind.
4. Bericht: Testausgabe, alle PROVISIONAL-Annahmen, jede Abweichung von Skizze oder Korrekturen
   mit Begründung, offene Fragen. Nichts davon selbst entscheiden.

---

## Abschnitt 11 – Nicht in Phase 1

- Temporal-Integration
- LangGraph
- Policy-Engine (außer der einfachen Python-Funktion)
- Echter LLM-Aufruf im Agent

---

## Abschnitt 12 – Offene Entscheidungen (nicht selbst treffen)

- Kanonisches JSON-Format: RFC 8785 oder sortierte Schlüssel? (im Prototyp dokumentieren, PROVISIONAL)
- Authorization-Ablaufzeit: konfigurierbarer Default (PROVISIONAL: 24 h)
- Maximale Proposal-Versionen je Request (PROVISIONAL: unbegrenzt im Prototyp)
- Kosten-Kriterium in der Risikoableitung: Schwellwerte und Währungseinheit (PROVISIONAL: deaktiviert)
- Reconciliation-Strategie für `DIVERGENT` nach Phase 1

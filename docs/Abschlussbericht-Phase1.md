# Sicherheitskern Phase 1 – Abschlussbericht

Stand: 2026-10-01 (Korrekturstand K8)
Basis: docs/Prototyp-Skizze-Phase1.md + Korrekturen K1–K8

---

## Testausgabe

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
hypothesis profile 'pmi_tests' -> deadline=None, suppress_health_check=(HealthCheck.function_scoped_fixture,)
rootdir: /home/user/PMI
plugins: hypothesis-6.168.3, anyio-4.14.1
collected 31 items

tests/test_crash.py::test_c0_crash_before_start_persist PASSED
tests/test_crash.py::test_c1_crash_before_rename PASSED
tests/test_crash.py::test_c2_crash_after_commit_before_db_ack PASSED
tests/test_crash.py::test_c3_crash_mid_authorization PASSED
tests/test_crash.py::test_c4a_crash_after_rename_before_commit PASSED
tests/test_crash.py::test_c5_divergent_on_foreign_commit PASSED
tests/test_crash.py::test_c6_crash_post_start_pre_write PASSED
tests/test_fault_inject.py::test_no_crash_without_activation PASSED
tests/test_fault_inject.py::test_crash_activates_with_matching_point PASSED
tests/test_fault_inject.py::test_no_crash_for_different_point PASSED
tests/test_invariants.py::test_i1_payload_change_after_approval_rejected PASSED
tests/test_invariants.py::test_i1_auth_reuse_rejected PASSED
tests/test_invariants.py::test_i1_expired_auth_rejected PASSED
tests/test_invariants.py::test_i2_raw_insert_rejected_by_begin_execution PASSED
tests/test_invariants.py::test_i2_valid_approve_path_succeeds PASSED
tests/test_invariants.py::test_i2_invalid_decided_by_prefix_rejected PASSED
tests/test_invariants.py::test_i3_started_to_failed_without_proof_rejected PASSED
tests/test_invariants.py::test_i3_started_to_failed_with_proof_allowed PASSED
tests/test_invariants.py::test_i3_applied_to_failed_always_rejected PASSED
tests/test_invariants.py::test_i3_hypothesis_no_failed_with_changed_revision PASSED
tests/test_invariants.py::test_i4_valid_proposal_accepted PASSED
tests/test_invariants.py::test_i4_extra_fields_rejected PASSED
tests/test_invariants.py::test_i4_decided_by_field_rejected PASSED
tests/test_invariants.py::test_i4_text_injection_has_no_effect PASSED
tests/test_invariants.py::test_i5_approve_without_person_rejected PASSED
tests/test_invariants.py::test_i6_adapter_requires_started_record PASSED
tests/test_invariants.py::test_i7_auth_single_use PASSED
tests/test_invariants.py::test_i8_agent_has_no_access_to_approval_path PASSED
tests/test_invariants.py::test_i8_calling_create_proposal_creates_no_authorization PASSED
tests/test_invariants.py::test_cas_revision_conflict PASSED
tests/test_invariants.py::test_cas_foreign_commit_between_approve_and_apply PASSED

======================== 31 passed in 149.57s (0:02:29) ========================
```

---

## Gegenprobe: Crash-Tests prüfen echtes Verhalten (C0–C6)

Für jeden Crash-Test wurde je eine zentrale Mechanismus-Invariante absichtlich gebrochen
(RUNS=2 für Geschwindigkeit). Alle 7 Tests gingen dabei rot.

| Test | Gebrochener Mechanismus | Erwartetes Rot-Symptom | Ergebnis |
|------|------------------------|------------------------|---------|
| C0 `crash_before_start_persist` | `maybe_crash("pre_start_persist")` deaktiviert | `rc != 0` schlägt fehl (Worker crasht nicht) | ROT ✓ |
| C1 `crash_before_rename` | `maybe_crash("pre_rename")` deaktiviert | `rc != 0` schlägt fehl | ROT ✓ |
| C2 `crash_after_commit_before_db_ack` | `_find_own_commit()` gibt immer `None` zurück | Retry erkennt eigenen Commit nicht → Status bleibt STARTED | ROT ✓ |
| C3 `crash_mid_authorization` | `maybe_crash("mid_authorization")` deaktiviert | `rc != 0` schlägt fehl | ROT ✓ |
| C4a `crash_after_rename_before_commit` | `maybe_crash("post_rename_pre_commit")` deaktiviert | `rc != 0` schlägt fehl | ROT ✓ |
| C5 `divergent_on_foreign_commit` | Dateiinhalt-Vergleich in `reconcile_applied_executions()` deaktiviert | Status bleibt APPLIED statt DIVERGENT/ESCALATED | ROT ✓ |
| C6 `crash_post_start_pre_write` | `maybe_crash("post_start_pre_write")` deaktiviert | `rc != 0` schlägt fehl | ROT ✓ |

---

## Semantik- und Teständerungen während der Implementierung

Diese Änderungen weichen von der ursprünglichen Skizze ab und wurden während der
Implementierung ohne vorherige Vorlage getroffen. **Künftig solche Änderungen
vor der Implementierung vorlegen** (Lessons L5).

| Änderung | Betroffene Datei(en) | Begründung |
|---------|---------------------|-----------|
| `APPLIED → APPLIED`-Übergang erlaubt (idempotent No-op) | `state_machine.py` | `adapter.apply()` mit ALREADY_APPLIED-Pfad ruft `transition(APPLIED)` auf einem bereits APPLIED-Datensatz auf. Ohne diesen Übergang wäre das ein Fehler, obwohl es korrekte Idempotenz ist. |
| `started_at` mit Millisekunden-Präzision (`strftime('%Y-%m-%d %H:%M:%f', 'now')`) | `db.py` | Crash-Tests legen mehrere Executions innerhalb einer Sekunde an. `ORDER BY started_at DESC LIMIT 1` war bei Sekunden-Granularität nichtdeterministisch. |
| `repo_path`-Parameter in `begin_execution()` | `state_machine.py` | Für I3: `transition(FAILED)` muss prüfen, ob HEAD sich seit Beginn geändert hat. Der Wert wird gespeichert, damit `transition()` ohne Aufrufer-Kontext darauf zugreifen kann. |
| Hypothesis-Test mit `tempfile.mkdtemp(dir=tmp_path)` statt `tmp_path / "h_repo"` | `test_invariants.py` | pytest's `tmp_path` ist function-scoped; hypothesis wiederverwendet es über 50 Iterationen. `mkdir()` schlägt ab Iteration 2 fehl. Fix: eindeutiges Unterverzeichnis pro Iteration. |

---

## Korrekturen K8 (nach Abschluss Phase 1, erster Korrekturlauf)

Diese Änderungen wurden nach dem ersten Abschlussbericht auf Anweisung vorgenommen:

| Korrektur | Beschreibung |
|-----------|-------------|
| **K8-1: expected_revision bei Request-Erstellung** | `expected_revision` wird jetzt bei `insert_request()` gesetzt und ist Teil des `request_hash`. `begin_execution()` liest den Wert aus dem `ExecutionRequest` statt ihn per `git rev-parse` zu ermitteln. Neuer Test: `test_cas_foreign_commit_between_approve_and_apply` (fremder Commit zwischen approve und apply → REVISION_CONFLICT). |
| **K8-2: DIVERGENT-Heuristik entfernt** | Die Heuristik „fremder Commit über PMI-Commit → DIVERGENT" in `apply()` wurde entfernt. DIVERGENT wird jetzt ausschließlich über `reconcile_applied_executions()` erkannt: Prüfung ob eigener Commit noch in der Historie ist **und** Dateiinhalt unverändert. Normale additive Commits (neue Dateien) lösen kein DIVERGENT aus. C5-Test auf diesen Mechanismus umgestellt. |
| **K8-3: Gegenprobe C0–C6 dokumentiert** | Für jeden Crash-Test wurde mit RUNS=2 ein Mechanismus absichtlich gebrochen und verifiziert, dass der Test rot wird (Tabelle oben). |

---

## PROVISIONAL-Annahmen

| Konstante | Wert | Begründung |
|-----------|------|-----------|
| `CANONICAL_JSON_SORT_KEYS` | `True` | RFC 8785 (kanonisches JSON) nicht implementiert; sortierte Schlüssel als einfache Annäherung. |
| `CANONICAL_JSON_SEPARATORS` | `(",", ":")` | Whitespace-freie Darstellung, kein Leerzeichen nach Doppelpunkt. |
| `AUTHORIZATION_TTL_SECONDS` | `86400` (24 h) | Noch keine Vorgabe; 24 Stunden als pragmatischer Wert. |
| `MAX_PROPOSAL_VERSIONS` | `None` (unbegrenzt) | Keine Vorgabe; unbegrenzt bis Phase 2 geklärt. |
| `COST_THRESHOLD_ENABLED` | `False` | Kosten-Kriterium in Phase 1 nicht spezifiziert. |
| `DIVERGENT_RECONCILIATION_STRATEGY` | `"none"` (bis K8-2: explizites Reconcile via `reconcile_applied_executions()`) | Keine automatische Reconciliation; muss explizit aufgerufen werden. DIVERGENT → ESCALATED, dann manuell. |
| `AUTHORIZATION_HMAC_SECRET` | aus Env-Var `PMI_AUTHORIZATION_HMAC_SECRET` | Format: beliebiger UTF-8-String, als Bytes encodiert. Länge und Rotation nicht spezifiziert. |

---

## Abweichungen von der Skizze

| Abweichung | Begründung |
|-----------|-----------|
| `expected_revision` Teil des `request_hash` | K8-1: Der Request bindet sich an einen konkreten Store-Stand. Fremder Commit nach Request-Erstellung → REVISION_CONFLICT (kein Schreibvorgang). |
| `begin_execution()` nimmt optionalen `repo_path` | Notwendig für Invariante I3: `transition(FAILED)` prüft ob Store-Revision seit Anfang geändert wurde. Wert wird in `Execution` gespeichert, nicht live abgerufen. |
| `Execution` hat zusätzliche Felder `repo_path`, `expected_revision` | Folge aus `repo_path`-Erweiterung und K8-1. |
| `Execution.started_at` mit Sub-Sekunden-Präzision (`strftime('%Y-%m-%d %H:%M:%f')`) | Deterministisches Ordering bei `ORDER BY started_at DESC LIMIT 1` in Tests, die mehrere Executions pro Sekunde anlegen. |
| `reconcile_applied_executions()` statt DIVERGENT-Heuristik in `apply()` | K8-2: Normale additive Commits dürfen künftige Requests nicht dauerhaft blockieren. Nur wenn eigener Commit nicht mehr in der Historie oder Dateiinhalt geändert → DIVERGENT. |
| Worker-Reconciliation: vorhandene STARTED-Execution wiederverwenden | Ohne Wiederverwendung des `idempotency_key` aus der DB würde `_find_own_commit()` bei Crash-Retry nicht greifen. |
| `maybe_crash("mid_authorization")` in `approval.py` | Für test_c3 notwendig; im Testplan als Fault-Point definiert aber nicht explizit in der Skizze verortet. |
| Hypothesis-Test: `tempfile.mkdtemp(dir=tmp_path)` statt `tmp_path / "h_repo"` | pytest's `tmp_path` ist function-scoped; hypothesis wiederverwendet das Verzeichnis über 50 Iterationen. `mkdir()` scheitert ab Iteration 2. |
| Hypothesis-Profil mit `deadline=None` und `suppress_health_check` | Git-Operationen überschreiten 200ms-Deadline; function_scoped_fixture-HealthCheck kollidiert mit hypothesis. |

---

## Offene Fragen (Skizze Abschnitt 12)

Diese Fragen wurden bewusst nicht entschieden. Die PROVISIONAL-Werte dienen als Platzhalter:

1. **Kanonisches JSON-Format**: Soll RFC 8785 umgesetzt werden, oder reicht `sort_keys=True`?
2. **Authorization-Ablaufzeit**: 24 Stunden sinnvoll? Abhängig von Nutzungsszenario.
3. **Maximale Proposal-Versionen**: Unbegrenzt oder mit Obergrenze?
4. **Kosten-Kriterium**: Schwellenwert und Felder noch nicht spezifiziert.
5. **DIVERGENT-Reconciliation**: Phase 1 eskaliert nur; Aufruf-Trigger für `reconcile_applied_executions()` (periodisch, nach jedem Apply?) noch offen.
6. **HMAC-Secret-Rotation**: Kein Rotations-Mechanismus in Phase 1. Schlüsselformat (Länge, Encoding) offen.
7. **CLI `pmi run`**: Stub-Implementierung; vollständige Ausführungsorchestrierung nicht spezifiziert.
8. **Proposal-Felder**: `action`, `path`, `scope`, `description` akzeptiert. Vollständiges Schema offen.

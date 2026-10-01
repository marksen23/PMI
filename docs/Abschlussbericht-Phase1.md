# Abschlussbericht – Prototyp Phase 1

Stand: 2026-10-01  
Basis: docs/Prototyp-Skizze-Phase1.md + Korrekturen K1–K7

---

## Testausgabe

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
hypothesis profile 'pmi_tests' -> deadline=None, suppress_health_check=(HealthCheck.function_scoped_fixture,)
rootdir: /home/user/PMI
plugins: hypothesis-6.168.3, anyio-4.14.1
collected 30 items

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

======================== 30 passed in 138.02s (0:02:18) ========================
```

Alle 7 Crash-Tests wurden zusätzlich mit absichtlich kaputtem `_find_own_commit` (immer `None`)
verifiziert: test_c2 geht dann rot → Tests prüfen echtes Verhalten.

---

## PROVISIONAL-Annahmen

| Konstante | Wert | Begründung |
|-----------|------|-----------|
| `CANONICAL_JSON_SORT_KEYS` | `True` | RFC 8785 (kanonisches JSON) nicht implementiert; sortierte Schlüssel als einfache Annäherung. |
| `CANONICAL_JSON_SEPARATORS` | `(",", ":")` | Whitespace-freie Darstellung, kein Leerzeichen nach Doppelpunkt. |
| `AUTHORIZATION_TTL_SECONDS` | `86400` (24 h) | Noch keine Vorgabe; 24 Stunden als pragmatischer Wert. |
| `MAX_PROPOSAL_VERSIONS` | `None` (unbegrenzt) | Keine Vorgabe; unbegrenzt bis Phase 2 geklärt. |
| `COST_THRESHOLD_ENABLED` | `False` | Kosten-Kriterium in Phase 1 nicht spezifiziert. |
| `DIVERGENT_RECONCILIATION_STRATEGY` | `"none"` | Keine automatische Reconciliation implementiert; DIVERGENT → ESCALATED, dann manuell. |
| `AUTHORIZATION_HMAC_SECRET` | aus Env-Var `PMI_AUTHORIZATION_HMAC_SECRET` | Format: beliebiger UTF-8-String, als Bytes encodiert. Länge und Rotation nicht spezifiziert. |

---

## Abweichungen von der Skizze

| Abweichung | Begründung |
|-----------|-----------|
| `begin_execution()` nimmt optionalen `repo_path` | Notwendig für Invariante I3: `transition(FAILED)` prüft ob Store-Revision seit `STARTED` geändert wurde. Ohne diesen Wert kann die Prüfung nicht stattfinden. |
| `Execution` hat zusätzliche Felder `repo_path`, `expected_revision` | Folge aus `repo_path`-Erweiterung oben. |
| `Execution.started_at` mit Sub-Sekunden-Präzision (`strftime('%Y-%m-%d %H:%M:%f')`) | Deterministisches Ordering bei `ORDER BY started_at DESC LIMIT 1` in Tests, die mehrere Executions pro Sekunde anlegen. |
| `DIVERGENT`-Erkennung in `GitProjectStoreAdapter.apply()` | Skizze beschreibt `APPLIED → DIVERGENT`, aber Erkennung muss im Adapter stattfinden: HEAD ist kein PMI-Commit, aber PMI-Vorfahren vorhanden → Fremd-Commit liegt über eigenem Commit. |
| Worker-Reconciliation: vorhandene STARTED-Execution wiederverwenden | Ohne Wiederverwendung des `idempotency_key` aus der DB würde `_find_own_commit()` bei Crash-Retry nicht greifen. |
| `maybe_crash("mid_authorization")` in `approval.py` | Für test_c3 notwendig; im Testplan als Fault-Point definiert aber nicht explizit in der Skizze verortet. |
| Hypothesis-Test: `tempfile.mkdtemp(dir=tmp_path)` statt `tmp_path / "h_repo"` | pytest's `tmp_path` ist function-scoped; hypothesis wiederverwendet das Verzeichnis über 50 Iterationen. `mkdir()` scheitert ab Iteration 2. Fix: unique Unterverzeichnis pro Iteration. |
| Hypothesis-Profil mit `deadline=None` und `suppress_health_check` | Git-Operationen überschreiten 200ms-Deadline; function_scoped_fixture-HealthCheck kollidiert mit hypothesis. |

---

## Offene Fragen (Skizze Abschnitt 12)

Diese Fragen wurden bewusst nicht entschieden. Die PROVISIONAL-Werte dienen als Platzhalter:

1. **Kanonisches JSON-Format**: Soll RFC 8785 umgesetzt werden, oder reicht `sort_keys=True`?
2. **Authorization-Ablaufzeit**: 24 Stunden sinnvoll? Abhängig von Nutzungsszenario.
3. **Maximale Proposal-Versionen**: Unbegrenzt oder mit Obergrenze?
4. **Kosten-Kriterium**: Schwellenwert und Felder noch nicht spezifiziert.
5. **DIVERGENT-Reconciliation**: Phase 1 eskaliert nur; automatische Wiederherstellung offen.
6. **HMAC-Secret-Rotation**: Kein Rotations-Mechanismus in Phase 1. Schlüsselformat (Länge, Encoding) offen.
7. **CLI `pmi run`**: Stub-Implementierung; vollständige Ausführungsorchestrierung nicht spezifiziert.
8. **Proposal-Felder**: `action`, `path`, `scope`, `description` akzeptiert. Vollständiges Schema offen.

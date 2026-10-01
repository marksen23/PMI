# Lessons Learned

Wird nach jeder Korrektur durch Mark ergänzt.

---

## L2 – Hypothesis + pytest tmp_path: Unique-Verzeichnis pro Iteration (2026-10-01)

`@given`-Tests mit `tmp_path`-Fixture (function-scoped) teilen dasselbe Verzeichnis über
alle Iterationen. `mkdir()` schlägt ab Iteration 2 fehl. Fix: `tempfile.mkdtemp(dir=tmp_path)`
für unique Unterverzeichnisse pro Iteration. Außerdem `deadline=None` setzen, wenn
Git-Operationen im Test vorkommen (überschreiten 200ms-Standard-Deadline).

---

## L3 – Reconciliation braucht idempotency_key aus der DB (2026-10-01)

Worker-Crash-Szenarien (C2, C4a, C6): Beim Retry muss der Worker die vorhandene
STARTED-Execution aus der DB lesen und deren `idempotency_key` wiederverwenden,
statt eine neue UUID zu generieren. Nur so findet `_find_own_commit()` den eigenen
Commit und gibt ALREADY_APPLIED statt eines Doppel-Commits zurück.

---

## L4 – Test-Invariante I3 (hypothesis) benötigt repo_path in begin_execution (2026-10-01)

`transition(FAILED)` kann nur prüfen, ob die Store-Revision geändert wurde, wenn
`begin_execution()` den optionalen `repo_path` erhält und die Anfangs-HEAD-SHA in
`Execution.expected_revision` speichert. Ohne diesen Wert ist der Prüfpunkt im
state_machine nicht erreichbar.

---

## L1 – Kein Rebase / Force-Push / Überschreiben ohne Freigabe (2026-10-01)

Bei git-Konflikten oder Divergenz zwischen lokalem Stand und Remote-Branch:
**Anhalten, Konflikt vorlegen, auf Marks ausdrückliche Freigabe warten.**
Kein `git rebase`, kein `git push --force`, kein Überschreiben von Marks Dateien
ohne explizite Zustimmung. Merge-Strategie und Auflösung immer mit Mark abstimmen.

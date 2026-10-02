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

## L5 – Sicherheitsinvariante im Kern, Test muss die Verletzung versuchen (2026-10-01)

Eine Lücke in `begin_execution` oder `insert_request` wird nicht im Temporal-Wrapper
geschlossen. Jeder andere Aufrufer, auch die CLI, liefe sonst daran vorbei.
Ein grüner Test, dessen Harness den Kernaufruf beim Retry auslässt, prüft die
Invariante nicht. Der Test muss den verbotenen Aufruf selbst machen und danach
Store und Datenbank lesen.

## L1 – Kein Rebase / Force-Push / Überschreiben ohne Freigabe (2026-10-01)

Bei git-Konflikten oder Divergenz zwischen lokalem Stand und Remote-Branch:
**Anhalten, Konflikt vorlegen, auf Marks ausdrückliche Freigabe warten.**
Kein `git rebase`, kein `git push --force`, kein Überschreiben von Marks Dateien
ohne explizite Zustimmung. Merge-Strategie und Auflösung immer mit Mark abstimmen.

---

## L6 – Auswertung der Kurzfassung Justitia 4.0 (2026-10-02)

Quelle nur die Kurzfassung (j40-auftrag-kurz-v2.0-20190522). Schema bleibt unverändert.
Auftrag 4 ist am 2026-10-02 erteilt. Der Live-Lauf ist am selben Tag erfolgt.
Sein Befund steht in L7.

Harte Pass/Fail-Kriterien: `meilensteine` nur `[]`, `budget_rahmen` nur `None`.
Abweichung ist unbelegt, auch wenn der Wert in Trainingsdaten oder einem späteren
Jahresbericht stimmt. Nutzenzahlen aus Kapitel 5 sind kein Projektbudget.

Belegtreue der Risikoliste, eigene Note: jede `beschreibung` muss einem der acht
Sätze aus Kapitel 7 zuordenbar sein. Eine neunte Beschreibung ist unbelegt.
Weglassen oder Doppeln ist ein Qualitätsvermerk.
Zuordnung: beide Seiten kleingeschrieben, Leerzeichen zusammengezogen, der Satz
steht in der Beschreibung oder die Beschreibung im Satz. Leere Beschreibung trifft
keinen Satz. Die Sätze stehen in `pmi/justitia_reference.py`.

Ergänzungsqualität, eigene Note: `wahrscheinlichkeit` und `schadensausmass` werden
als vom Modell ergänzt markiert. `ursache`, `auswirkung`, `massnahme` und
`verantwortlich_vorschlag` müssen zum Risikosatz passen, ohne erfundene Zahlen,
Termine oder Rollen. Allgemeine Floskeln gelten als schwach.

---

## L7 – max_tokens und der Live-Lauf (2026-10-02)

`ClaudeLLMProvider` sendet `max_tokens=2048`. Der Lauf der Kurzfassung endete mit
`stop_reason=max_tokens` nach 4266 Zeichen, mitten in den Stakeholdern. Ein zweiter
Lauf mit 8192 Token endete ebenfalls mit `max_tokens`, im elften Risiko. Beide
Ausgaben sind kein gültiges JSON. `evaluate()` hat sie abgewiesen. Meilensteine,
Budget und die Beschreibungen wurden aus dem abgebrochenen Text gelesen;
`zuordnung()` hat die elf Beschreibungen geprüft.

Harte Kriterien nicht bestanden: `meilensteine` enthielt acht Einträge, abgeleitet
aus Fussnote und Bildunterschrift. `budget_rahmen` enthielt die Nutzenzahlen aus
Kapitel 5. Drei Beschreibungen ausserhalb von Kapitel 7 sind unbelegt. „Projektleitung“
ist schwach. Die Zahl 25'000 steht im Text (15'000 Arbeitsplätze und 10'000 Anwälte)
und ist für die menschliche Note belegbar; die mechanische Ziffernmarkierung bleibt.

Mark hat 16384 am 2026-10-02 freigegeben. `ClaudeLLMProvider` sendet diesen
Wert (`d4565b5` auf `cursor/agent-llm-provider`). Der Lauf damit endete mit
`stop_reason=end_turn`. `evaluate()` hat ein gültiges `ProposalOutput` bewertet.
Hart nicht bestanden: sieben Meilensteine, `budget_rahmen` mit den Nutzenzahlen.
15 Beschreibungen, drei Treffer nach der Zeichenkettenregel, zwölf unbelegt.
Keine Authorization. Der Schlüssel bleibt aus dem Repository und aus dem Chat.

---

## L8 – Auswertung IHP DMS, TED 274205-2025 (2026-10-02)

Mark hat am 2026-10-02 die umgekehrte Belegprobe freigegeben. Sie gilt nur für
diesen Vergabebescheid. Die Justitia-Regel aus L6 bleibt für die Kurzfassung.
Der Prüfer ist nicht beauftragt. Ein Live-Lauf hat nicht stattgefunden.

Quelle ist der TED-Bescheid 274205-2025 (can-standard). Auftraggeber ist die
IHP GmbH. PayableAmount und TotalAmount sind 317829.00 EUR, Zuschlag an die
Net Con Ausgabeservice GmbH. HigherTenderAmount 858074.65 EUR ist das höchste
eingegangene Angebot, keine Zulässigkeitsgrenze. LowerTenderAmount ist
317829.00 EUR. Die Laufzeit steht als StartDate 2024-12-20 und EndDate
2028-12-31. IssueDate ist 2025-04-28, PublicationDate ist 2025-04-29.
AwardDate 2000-01-01Z ist der leere eForms-Platzhalter, kein Projekttermin.

Hart, nur dieses Dokument. `budget_rahmen` enthält 317829.00 EUR als Zuschlag.
Leer oder ein anderer Betrag ist Fail. 858074.65 EUR darf nur als höchstes
eingegangenes Angebot stehen. Die beiden Laufzeitdaten müssen vorkommen.
Leere `meilensteine` sind Fail. Jedes weitere Datum, auch der Platzhalter
2000-01-01, ist unbelegt. IssueDate und PublicationDate sind keine der beiden
verlangten Daten. Risiken bleiben eine Qualitätsnotiz. Erfundene Zahlen, Daten
oder Rollen sind schwach. Das Schema bleibt unverändert.

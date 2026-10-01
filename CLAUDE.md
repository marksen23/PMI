# Prototyp Phase 1 – Arbeitsregeln

Referenz: docs/Prototyp-Skizze-Phase1.md (Inhalt), Korrekturen im jeweiligen Auftrag haben Vorrang.

## Arbeitsweise
- Vor jeder Aufgabe mit 3+ Schritten: Plan in tasks/todo.md schreiben und auf Freigabe warten.
- Tests vor Implementierung. Testfälle für Invarianten zuerst vorlegen, erst nach Freigabe implementieren.
- Nach jeder Korrektur durch Mark: Regel in tasks/lessons.md ergänzen.
- Fertig heißt: alle Tests grün, Ausgabe der Testläufe im Bericht. Nie "fertig" ohne Nachweis.

## Verbote
- Keine neuen Konzept- oder Spezifikationsdokumente. Erlaubt: Code, Tests, README,
  und bei Abweichung von der Skizze ein Eintrag in docs/decisions.md (max. 10 Zeilen, mit Begründung).
- Offene Entscheidungen (Skizze Abschnitt 12) nicht selbst treffen. Annahmen in config.py
  als PROVISIONAL markieren und im Bericht auflisten.
- Keine LLM-Ausgabe darf auf einem Codepfad eine Authorization erzeugen oder einen Status setzen.
- decided_by nur mit Präfix human:. Agenten- und Service-Identitäten werden abgewiesen.
- In Invarianten- und Absturztests nichts am Kern mocken: echte SQLite-DB, echtes git-Repo in tmp.

## Technik
- Python, kompatibel ab 3.11 (keine f-String-Ausdrücke mit Backslash o. Ä.).
- pytest + hypothesis. SQLite. git über subprocess.
- Die Kontroll-DB ist die maßgebliche Quelle für Governance-Zustände.

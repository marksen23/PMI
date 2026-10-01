# Lessons Learned

Wird nach jeder Korrektur durch Mark ergänzt.

---

## L1 – Kein Rebase / Force-Push / Überschreiben ohne Freigabe (2026-10-01)

Bei git-Konflikten oder Divergenz zwischen lokalem Stand und Remote-Branch:
**Anhalten, Konflikt vorlegen, auf Marks ausdrückliche Freigabe warten.**
Kein `git rebase`, kein `git push --force`, kein Überschreiben von Marks Dateien
ohne explizite Zustimmung. Merge-Strategie und Auflösung immer mit Mark abstimmen.

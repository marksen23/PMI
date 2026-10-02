# Entscheidungen

## D1 – REVISION_CONFLICT setzt FAILED neben transition() (2026-10-01)

`transition(FAILED)` bleibt verboten, wenn HEAD von `expected_revision` abweicht (I3).
`fail_revision_conflict` setzt FAILED nur, wenn dieser Konflikt besteht und kein Commit
den Idempotenzschlüssel trägt. Sonst bliebe die Execution in STARTED, die Authorization
aber verbraucht. Auftrag 1b.

## D2 – workflow_id am ExecutionRequest (2026-10-02)

`pmi run` signalisiert den laufenden Workflow. Die ID steht in `ExecutionRequest.workflow_id`
und wird im selben INSERT wie der Request geschrieben. Ein gesetzter Wert wird nicht
überschrieben. Die Skizze kennt die Spalte nicht.

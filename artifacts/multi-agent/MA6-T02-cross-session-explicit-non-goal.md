# MA6-T02 — Cross-session Agent Memory Explicit Non-goal

## Boundary decision

Cross-session Agent memory is explicitly **out of scope** for the current MA6
implementation sequence. In particular, MA6 must not silently implement either
of these behaviors:

```text
Reviewer remembers a candidate's weaknesses across months of interviews
ReportCoach tracks growth across multiple interview sessions
```

Those behaviors require a separate product and architecture decision named:

```text
Cross-session Agent Memory Governance
```

## Required future governance

If that separate capability is proposed, approval must cover all of the
following before implementation:

```text
consent
principal ownership
tenant isolation
retention
export
delete
revocation
```

No MA6 task may infer or substitute any of these controls. In particular,
session-local Agent memory must not be promoted to cross-session memory by
changing a retention setting, reusing a principal-level store, or widening a
query scope.

## Scope distinction

This non-goal applies to **Agent-private memory** only. Existing principal-level
memory facilities and their separately governed consent/export/deletion
surfaces are not modified by this Task and must not be used as an implicit
Agent-private cross-session store.

## Acceptance

```text
cross-session Agent memory explicitly prohibited: PASS
Reviewer cross-session weakness tracking prohibited: PASS
ReportCoach cross-session growth tracking prohibited: PASS
separate governance decision required: PASS
consent/ownership/tenant/retention/export/delete/revocation listed: PASS
no implementation change introduced: PASS
```

```text
MA6-T02 = PASS
NEXT_TASK = NOT_STARTED
```


# Memory Long-context Quality Acceptance

Status: deterministic gate implemented; real-provider evaluation not authorized.

The committed `memory-long-context-v1` fixture is entirely synthetic. It covers 20-turn Chinese, English, and mixed-language interviews with numbers, percentages, dates, capacity limits, Python/Java/SQL identifiers, negation, corrections, appended answers, unresolved topics, skipped questions, provider fallback, superseded Question Memory, deleting/deleted sources, and prompt-injection strings.

Hard invariants require 100% preservation of the mandatory current answer, grounded source excerpts, grounded numbers and identifiers, correction precedence, deletion/revocation filtering, zero Question Memory scoring evidence, zero Principal Memory Prompt injection, zero cross-principal contamination, and zero known-over-budget provider calls.

Initial semantic thresholds are atomic fact recall >= 95%, unresolved topic recall >= 90%, unsupported atomic claim rate = 0, and route conclusion conflicts = 0. These checks do not claim to prove unrestricted natural-language semantic equivalence. Human or separately authorized model-judge review remains a distinct activity.

Run:

```powershell
& 'F:\python3.11\python.exe' -m scripts.evaluate_memory_quality --deterministic
```

`--real-provider` fails closed unless a separate provider, dataset, budget, and redacted-output authorization workflow is supplied; this repository phase does not grant that authorization.

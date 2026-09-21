# MA8-T08 - Architecture Gate

## Final Boundary Matrix

| Required boundary | Scope | Violations | Result |
| --- | --- | ---: | --- |
| Domain -> infrastructure | internal adapters/application/graphs/runtime/services/A2A plus classified external infrastructure packages | 0 | PASS |
| Application -> adapters | all `app.application` modules | 0 | PASS |
| Application -> runtime | all `app.application` modules | 0 | PASS |
| Application -> A2A implementation | all `app.application` modules | 0 | PASS |
| Ports -> A2A implementation | all `app.ports` modules | 0 | PASS |

The final contract scans the whole application tree with the repository's AST
dependency scanner. It does not limit Application checks to the Scheduler
subpackage. Parse errors and unresolved relative imports fail the Gate.

Domain infrastructure checks also classify direct external imports such as
PostgreSQL, LangGraph, queues, provider SDKs, and provider transports through
the existing infrastructure scanner. General-purpose domain libraries such as
Pydantic are not misclassified as infrastructure implementations.

## Detector Proof

A synthetic fixture injects six forbidden edges covering all five required
rules:

```text
Domain -> adapter
Domain -> external PostgreSQL package
Application -> adapter
Application -> runtime
Application -> A2A implementation
Ports -> A2A implementation
```

All six edges are detected and aggregate to the five required boundary rules.
This prevents a zero result caused by a scanner that does not recognize the
forbidden dependency shapes.

## Repository Evidence

```text
app_files_scanned = 513
test_files_scanned = 465
internal_edge_count = 3493
external_edge_count = 2640
parse_error_count = 0
unresolved_relative_import_count = 0
classified infrastructure violations = 0
cross_layer_violation_pairs = 0
final_architecture_definition_satisfied = true
```

## Verification

```text
dedicated final five-boundary Gate: 2 passed
architecture full suite: 127 passed
all acceptance tests: 225 passed
Python compileall (app + tests): PASS
```

The reported Pydantic JSON-schema warnings are pre-existing dependency warnings
and do not represent architecture-boundary failures.

## Acceptance

```text
Domain -> infrastructure = 0: PASS
Application -> adapters = 0: PASS
Application -> runtime = 0: PASS
Application -> A2A implementation = 0: PASS
Ports -> A2A implementation = 0: PASS
```

```text
MA8-T08 = PASS
NEXT_TASK = NOT_STARTED
```

# MA9 Final Architecture Snapshot

Status: MA9 CURRENT - production closure architecture evidence

## Counting Rules

- P0 is pinned to commit `0af2a8b`.
- Services LOC uses the official P0 non-empty-line baseline; MA9 uses the same definition.
- God-module and largest-module LOC are physical lines including blanks and comments.
- Cross-layer violations are unique `(rule, source module, target module)` ratchet pairs.
- Dependency cycles are strongly connected components containing at least two modules.
- Duplicate implementations are exact normalized top-level AST-body groups.
- LOC is an outcome metric, not proof of architecture quality.

## P0 vs MA9

| metric | P0 | MA9 | delta |
| --- | ---: | ---: | ---: |
| App Python files | 418 | 522 | +104 |
| App physical LOC | 118,413 | 131,412 | +12,999 |
| App non-empty LOC | 108,150 | 119,496 | +11,346 |
| Services Python files | 220 | 0 | -220 |
| Services non-empty LOC | 75,970 | 0 | -75,970 |
| Cross-layer violation pairs | 19 | 0 | -19 |
| Dependency cycle groups | 2 | 4 | +2 |
| Modules in dependency cycles | 123 | 8 | -115 |
| Exact duplicate implementation groups | 19 | 28 | +9 |
| Largest module physical LOC | 2,459 | 2,965 | +506 |

Largest modules:

- P0: `app.services.runtime` (2,459 LOC from pinned commit).
- MA9: `app.runtime.composition` (2,965 LOC).

## God-Module Owners

| responsibility | P0 owner | P0 LOC | MA9 owner | MA9 LOC | delta |
| --- | --- | ---: | --- | ---: | ---: |
| Runtime composition | `app.services.runtime` | 2,459 | `app.runtime.composition` | 2,965 | +506 |
| Durable interview graph | `app.graphs.durable_interview_graph` | 2,296 | `app.graphs.durable_interview_graph` | 2,303 | +7 |
| LLM provider | `app.services.llm` | 1,507 | `app.adapters.providers.llm` | 1,440 | -67 |

## Remaining Boundary Signals

| signal | final value | required |
| --- | ---: | ---: |
| Domain to infrastructure edges | 0 | 0 |
| Application to adapters edges | 0 | 0 |
| Application to services edges | 0 | 0 |
| Application to Runtime edges | 0 | 0 |
| Application to LangGraph edges | 0 | 0 |
| Ratchet legacy violation pairs | 0 | 0 |
| `app/services` exists | no | no |

## Decision

The MA9 architecture snapshot satisfies all measured boundary signals. The historical P9 report remains separately frozen.

This MA9 snapshot records current architecture metrics without overwriting the frozen P9 closure evidence. Protected PostgreSQL reliability remains deferred pending explicit approval, and the full production reliability gate remains NOT_VERIFIED.

Large composition/graph modules, remaining cycles, and duplicate groups are Future Hardening items rather than MA9 closure blockers.

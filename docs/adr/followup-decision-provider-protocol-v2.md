# ADR: Follow-up Decision Provider Protocol V2

- Status: Accepted for the Interview Quality V1 candidate
- Date: 2026-08-07
- Decision prompt version: `followup-decision-v2`
- Decision prompt SHA-256:
  `64bac5384e0470deb937f32ac8046dd0925d5e7549b9c74764844a7bce932028`

## Context

The durable Follow-up Decision contract remains the two-stage design documented in
`followup-decision-v1.md`: one bounded Decision is persisted before any optional
question-generation request. The Provider transport for that Decision needs a
model-specific capability choice before the first outbound request.

The authorized `deepseek-v4-pro` endpoint does not support the repository's
LangChain `json_schema` request path with complete response metering. Attempting a
structured request and then falling back to raw JSON would create two outbound
requests and could leave the first request without trustworthy usage metadata.

## Decision

All production and T36 evaluation callers construct the Decision Provider through
one shared factory. The factory requires an exact configured model identity and
selects the protocol before any Provider request:

```text
deepseek-v4-pro -> raw_only
every other exact model -> structured_first
missing or empty model identity -> fail closed before request
```

The mapping is exact and case-sensitive. It does not use model prefixes, aliases,
environment fallback, or an exception-triggered second request. T36 evaluation uses
the model frozen in the Provider authorization. Legacy and durable production paths
use the model frozen in `llm.config`.

The `raw_only` request sequence is:

```text
bind(max_tokens=300)
-> begin Provider attempt
-> invoke exactly once
-> publish response usage and model metadata
-> parse the complete response as strict JSON
-> validate DecisionContract
-> enforce exact response model identity
```

No parse, schema, or model failure sends a fallback request. A transport failure
before a response remains an attempted but unmetered request and causes evaluation
to stop. Once a response exists, usage is published before JSON or schema
validation, so an invalid response retains its safe accounting metadata.

A response-model mismatch is distinct from ordinary invalid output. The Decision
attempt is recorded as a terminal `provider_model_mismatch` failure with its safe
usage and hashed response identity, then the error propagates through legacy and
durable production callers. It cannot be converted into a local fallback Decision,
retried by the Decision service, or followed by another Provider request.

## JSON and schema boundary

The V2 prompt embeds the canonical `DecisionContract.model_json_schema()` and is
hashed together with all JSON-only instructions. It requires one JSON object and
rejects Markdown fences, surrounding prose, trailing data, arrays, scalars,
duplicate keys, extra fields, and invalid cross-field Decision semantics.

The persisted evidence never includes the raw Provider response. It may include the
validated Decision, normalized input/output/cached token counts, the exact response
model, and a hashed Provider response identity.

Usage normalization is shared with the global Provider accounting path. For a T36
complete real saved artifact and its formal-evidence eligibility, a request is fully
metered only when input tokens, output tokens, cached input tokens, and the exact
authorized model are all present. Unknown or partial token metadata is not coerced
to zero. Other global consumers may apply their own explicitly documented evidence
thresholds; this ADR does not silently redefine those consumers.

The T65 formal executor manifest explicitly binds the durable graph,
`followup_diagnostics.py`, `followup_prompts.py`, and `provider_usage.py`, in
addition to the Decision service and runtime wiring. Omitting or changing one of
those files invalidates the formal executor receipt. This fixed security-relevant
surface is not represented as a complete transitive Python dependency closure; the
candidate revision/tree remains the binding for the full repository.

## Evidence consequence

This protocol changes the Decision prompt version/hash and the implementation tree.
T64, T36, T57, or T65 artifacts bound to an earlier revision remain immutable
historical evidence and cannot be attributed to the V2 candidate. The new candidate
must pass its own candidate-bound Engineering matrix before any live T36 smoke/full
capture. A successful Provider capture still does not replace the required
independent human Quality review or formal external Gate authority.

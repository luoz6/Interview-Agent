# Interview Quality V1 Execution Amendment: Follow-up Decision V2

- Date: 2026-08-07
- Status: implementation in progress; new candidate not yet frozen
- Active Plan: `v0.2.3`
- Active Plan SHA-256:
  `446bb28746aee10fe9b79932cc585b6f734cc054b5be0fa52f409e5942f08f29`
- Provider authorization: `interview-quality-v1-20260807-unlimited-02`
- Provider authorization SHA-256:
  `0a0d7576cc26b94da7abe4c880408358a29cd2a0472e54f5814f1d2fec28670a`
- Authorized model: `deepseek-v4-pro`
- GateConfig SHA-256:
  `2b650efab1242c00d8e501046fba985ad6a6db191d6043058be0671f9f851535`

## Why this amendment exists

The active Plan file remains byte-for-byte unchanged because its SHA-256 is already
bound by the execution manifest and Provider preflight. During the authorized T57
smoke, the exact model demonstrated that the LangChain `json_schema` transport can
fail before trustworthy usage metadata is returned. T57 initial-plan generation was
corrected to choose a one-request raw JSON protocol before the request.

The T36 follow-up Decision path used the same unsupported structured transport but
had not yet received the same capability correction. Running a T36 smoke on that
path would knowingly create an attempted but unmetered request. This append-only
execution amendment records the required safety correction without rewriting the
authorized Plan or any historical evidence.

## Implementation boundary

The correction is defined by
`docs/adr/followup-decision-provider-protocol-v2.md`:

- `deepseek-v4-pro` selects Decision `raw_only` before the first request;
- all production and T36 evaluation callers use the same exact-model resolver;
- missing model identity fails before a request;
- raw Decision output is one strict JSON object validated by `DecisionContract`;
- parsing, schema, usage, or model failures never trigger a fallback request;
- incomplete token metadata remains unknown and hard-stops Provider evaluation;
- response model mismatch terminally fails the Decision attempt and propagates;
- the T65 exact executor surface includes the durable graph, prompt protocol, and
  Provider usage normalizer.

No T57 question review, dataset eligibility annotation, independent-human identity,
or formal external authority decision is generated or changed by this amendment.

## Candidate and evidence consequences

This implementation changes production code, prompt lineage, and the formal T65
executor surface. Therefore all earlier artifacts remain append-only historical
evidence for their original revision/tree. In particular, the `091a70c` T64 matrix
and T57 Provider capture, the V1 T36 evidence, and the earlier T65 evidence/review
cannot be relabeled as evidence for the new candidate.

After the implementation is reviewed and committed, the required order is:

1. Freeze the new clean revision and tree.
2. Run focused T33/T36/T63 offline regression checks and the full engineering suite.
3. Execute the formal candidate-bound T63 acceptance and produce new T63 evidence.
4. Rerun the full isolated Windows T64 matrix.
5. Rerun the full isolated Ubuntu T64 matrix and cross-platform Gate.
6. Only after T64 Engineering PASS, run a fresh T36 Provider smoke.
7. If the smoke has complete exact-model metering and no hard stop, run T36 full.
8. Rerun candidate-bound T57 capture where the publication contract requires a
   single implementation revision/tree.
9. Complete the candidate-bound T27 report-scoring Quality work, which is currently
   `QUALITY_NOT_RUN`, or satisfy an explicitly verified replacement contract.
10. Rebuild the T65 executor manifest, execute the complete T65 formal Provider
    benchmark for the same candidate, and generate new receipts/evidence; rebuilding
    the manifest alone is not a T65 run or PASS. Do not combine old V1 and new V2
    sources.
11. Keep T36/T57/T65 Quality blocked until real independent-human review and trusted
   external authority evidence are complete.
12. Build T67 Quality RC and T69/T71 publication/freeze evidence only after every
    candidate-bound prerequisite is present and hash-consistent.

## Historical evidence policy

Existing PASS, FAIL, BLOCKED, smoke, and full artifacts must not be deleted,
overwritten, edited in place, or rebound to the new candidate. New runs use new
append-only run directories. Engineering PASS, Provider capture completeness, and
Quality/formal PASS remain separate states.

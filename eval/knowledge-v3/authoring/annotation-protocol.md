# Knowledge Eval V3 annotation protocol

Protocol version: `knowledge-eval-v3-annotation-protocol-2026-08-13-v1`

## Purpose

Create a new Chinese retrieval evaluation dataset that measures retrieval
quality without leaking Legacy or Hybrid output into labels. The protocol
produces human evidence; this blank repository package does not substitute for
that evidence.

## Roles

The case author writes a natural Chinese interview-retrieval query and assigns
the intended case type and evaluation group. Annotator A and Annotator B are
qualified backend interviewers who independently label relevant, related, and
excluded chunks. The adjudicator resolves differences. The holdout owner keeps
the 25 holdout cases sealed and is not a Hybrid tuner.

Annotator identity is stored only as SHA-256. Raw identity, notes, query text,
and source-system audit records remain in the restricted annotation system.

## Frozen design

- Total cases: 100.
- Tuning: 75; holdout: 25.
- Case families are frozen before labeling and cannot cross splits.
- Every one of the 14 V3 case types has at least three cases.
- Every case receives two independent blinded annotations.
- Annotators must not see Legacy candidates, Hybrid candidates, scores,
  rankings, traces, selected evidence, or prior labels.

If a case must be rewritten so materially that its information need changes,
retire the slot and issue a new authoring package revision. Do not silently
change a frozen holdout query.

## Case authoring

Write one concise Chinese information need, at most 500 characters. Do not copy
queries from the historical 12-case pilot, 18-case Memory P1 set, knowledge
question patterns, production logs, resumes, job descriptions, or model output.
The query may contain an English acronym or exact technical term, but it must
contain Chinese text.

Select canonical tags, source types, and allowed domains from the corpus
catalog and the V3 evaluation-group mapping. These are case intent metadata,
not retrieved results. For `out_of_domain` and `no_evidence`, the human labels
must explicitly determine whether `expected_no_evidence` is true; the blank
scaffold does not pre-decide it.

## Independent labeling

Each annotator records three pairwise-disjoint sets:

- primary relevant chunks directly answer the information need;
- accepted related chunks add useful but non-primary evidence;
- excluded chunks are plausible confusers that should not be returned.

An evidence-bearing case needs at least one primary chunk. A case marked
`expected_no_evidence=true` must have no primary or accepted-related chunks and
must use case type `no_evidence` or `out_of_domain`. Annotators must judge the
catalog and controlled source material, never engine output.

Canonicalize each completed independent record and store its SHA-256. Neither
annotator may see the other record before both hashes are frozen.

## Adjudication and agreement

Pre-register the agreement metric and minimum before labeling begins. Compute
the observed agreement from the two frozen independent label sets; do not
invent or backfill an agreement value. If the minimum is not met, the dataset
does not pass release validation.

The adjudicator reviews both frozen records, records the consensus label sets,
and freezes a consensus-record SHA-256. The final dataset contains hashed
annotator identities, independent record hashes, and the consensus hash, not
raw reviewer identities or notes.

## Holdout control

Only the holdout owner and blinded annotators may access authored holdout data.
Hybrid tuning uses tuning only. Freeze the final Hybrid profile, candidate code
identity, and threshold policy after the Legacy holdout artifact exists and
before Hybrid holdout is opened. Any corpus, model revision, profile, code, or
dataset identity drift invalidates the registration.

## Release checks

Before producing a runnable V3 dataset, verify all of the following:

- 100 unique case IDs, 75 tuning and 25 holdout;
- every family belongs to exactly one split;
- all 14 case types are represented at the registered quotas;
- all referenced chunk IDs exist in the bound RocketMQ V4 manifest;
- two independent annotation records and one consensus record exist per case;
- the observed agreement meets the pre-registered minimum;
- governance timestamps are timezone-aware; record the frozen split and start
  of labeling at the same controlled handoff instant to satisfy the V3
  governance timeline contract;
- the final dataset canonical SHA-256 and provenance record are archived;
- no query text or knowledge body enters runtime evaluation artifacts.

Passing these checks authorizes offline V3 evaluation only. It does not by
itself authorize Hybrid runtime selection, Shadow, Canary, production rollout,
or Legacy retirement.

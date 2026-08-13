# Interview Quality V1 dataset contract fixtures

This directory freezes four dataset families, their versioned revisions, and their
common machine schema. All checked-in inputs are synthetic, but the directory now
contains both small contract fixtures and larger construction sets. A contract fixture
has `fixture_only=true`; a construction set has `fixture_only=false` but still remains
`gate_eligible=false` until its independent review is complete. Passing schema or
synthetic replay tests is never by itself a Quality Gate result.

`initial-question-quality-v2.json` is the T57 non-fixture construction set. It
contains 12 synthetic JD/resume combinations, two required generations per case,
six role domains, all three difficulties, all four focus presets, and all four
duration presets. It remains `gate_eligible=false` while independent review is
pending. Its deterministic builder is `scripts/build_initial_question_dataset.py`.
The T57 evaluator is `scripts/evaluate_initial_question_quality.py`; it supports
synthetic fixture replay, immutable saved-output replay, and a fail-closed real
Provider mode. Provider mode validates the unified authorization, frozen hashes,
redaction, exact model identity, pricing, per-request usage, and local evidence
persistence before or between outbound requests.

`followup-decision-quality-v2.json` is the T35 non-fixture construction set:
it contains 100 synthetic cases, including 20 complete two-step sequences and
20 adversarial cases. It remains `gate_eligible=false` because all annotations
are still `pending` independent review. Its existence proves T35 Engineering
coverage, not Gate 3 Quality PASS. The deterministic builder is
`scripts/build_followup_decision_dataset.py`; rebuilding must reproduce the
same case hashes and refresh only the dataset file manifest.

The common schema requires case identity/version, language, case and question type,
difficulty, quality label, source boundary, expected action or score range, must-have
evidence, forbidden inference, annotation/review/dispute state, train/dev/blind-test
partition, Provider permission, and canonical content hashes.

Partition policy:

- `train` may influence implementation and Prompt design;
- `dev` may tune thresholds only through the GateConfig change process;
- `blind-test` is sealed from tuning and opened only for acceptance;
- one case ID/version appears in exactly one partition;
- unresolved disputes and unreviewed cases are never gate eligible.

Only synthetic, public, or deterministically/manually redacted inputs are permitted.
Real candidate identity/contact data, unredacted resumes or answers, employer secrets,
production exports, credentials, and Principal Memory are prohibited.

Each case stores a SHA-256 over canonical input and over canonical case content with
the `hashes` object excluded. Overall file SHA-256 values are stored in
`manifest.json`; a file cannot truthfully contain its own full-file hash without a
self-reference. Changing any dataset file requires a new versioned file and manifest
entry rather than overwriting acceptance evidence.

# Interview Quality V1 dataset contract fixtures

This directory freezes the four dataset identities and their common machine schema.
The checked-in records are synthetic contract fixtures only, not completed calibrated
quality sets. Every fixture has `fixture_only=true` and `gate_eligible=false`; passing
their schema tests is not a Quality Gate result.

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

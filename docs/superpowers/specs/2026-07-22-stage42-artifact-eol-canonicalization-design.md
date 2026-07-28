# Stage 42 Artifact EOL Canonicalization Design

## Context

The Stage 42 acceptance manifest records the canonical LF bytes committed to
Git. On Windows, the system Git configuration uses `core.autocrlf=true`, so a
clean checkout expands LF to CRLF in tracked Markdown and JSON artifacts. The
current audit hashes raw working-tree bytes and therefore reports a manifest
mismatch even though Git reports a clean tree and the artifact content is
unchanged.

The three affected files differ from the manifest by exactly one byte per line.
Replacing CRLF with LF reproduces their recorded sizes and SHA-256 hashes.

## Decision

The Stage 42 artifact inventory will canonicalize line endings for `.json` and
`.md` files before calculating `size` and `sha256`. Canonicalization replaces
CRLF with LF at the byte level. Other bytes are unchanged.

Binary artifacts and all other suffixes remain byte-for-byte strict. The
manifest is not regenerated, and the committed Stage 42 evidence is not
modified.

## Boundaries

- Change only `scripts/audit_stage42_artifacts.py` and its focused tests.
- Keep the whitelist, required directory, passing-metrics, and privacy checks
  unchanged.
- Do not make JSON parsing or Markdown rendering part of inventory hashing.
- Do not normalize lone carriage returns or perform Unicode normalization.
- Do not change the Stage 44A artifact auditor, whose artifacts are generated
  and audited within one run.

## Data Flow

For each whitelisted artifact, the inventory helper reads bytes once. For
`.json` and `.md`, it derives canonical bytes with `CRLF -> LF`; for all other
files it uses the original bytes. Both size and SHA-256 are computed from the
same selected byte sequence.

Privacy scanning continues to decode the original file as UTF-8 and therefore
retains its current detection behavior.

## Failure Behavior

Changing text content still changes the canonical hash and fails the audit.
Changing binary bytes still fails the audit. LF and CRLF representations of the
same tracked text are intentionally equivalent because Git may produce either
representation from the same repository content.

## Verification

Add a regression test that writes the manifest from LF text, converts the
tracked text artifacts to CRLF, and proves the audit still passes. Retain the
existing changed-artifact test to prove semantic text mutations fail. Run the
focused Stage 42 audit suite, the real saved Stage 42 audit command, the Stage
44A focused suite, the complete Python suite, and `git diff --check` before the
Stage 44A acceptance record can become `PASS`.

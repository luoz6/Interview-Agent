# Local V1 platform-specific Python lock ADR

**Status:** Accepted
**Date:** 2026-08-04
**Scope:** Windows 11 x64 and Ubuntu 24.04 LTS x64, Python 3.11

## Context

Local V1 previously used one `requirements.lock.txt` generated on Windows.
That lock included Windows-only `colorama` resolution but omitted Linux
`uvloop`. H6 then regenerated the same source twice with pinned pip 25.1.1 and
pip-tools 7.6.0 on Ubuntu 24.04/Python 3.11. The two Linux outputs were
byte-identical, but pip-tools correctly evaluated environment markers and
omitted the false Windows branch. The resulting Linux lock therefore could not
prove a clean Windows `--require-hashes` installation.

Manually copying package blocks or hashes between generated files is rejected:
it would make the lock appear universal without a supported resolver producing
that result.

## Decision

- `requirements.txt` remains the single reviewed dependency source.
- `requirements-tooling.txt` pins pip 25.1.1 and pip-tools 7.6.0.
- `requirements-windows.lock.txt` is generated on Windows 11 x64 with Python
  3.11.
- `requirements-linux.lock.txt` is generated on Ubuntu 24.04 LTS x64 with
  Python 3.11.
- `requirements.lock.txt` remains a byte-identical Windows compatibility alias
  for existing Local V1 automation. New automation must select the named
  platform lock explicitly.
- Every generated lock must be regenerated twice with the same output name and
  compared byte-for-byte before publication.
- `requirements.lock.meta.json` binds the source, tools, and generated lock
  digests. Contract tests reject a source/lock/metadata mismatch.

## Consequences

Windows never attempts to install `uvloop`; Ubuntu receives the pinned
`uvloop` release and hashes. Both clean installs retain `--require-hashes` and
`pip check`. A dependency change requires regenerating and validating both
locks. Other operating systems remain `UNTESTED`; neither lock is evidence of
support outside its named platform.

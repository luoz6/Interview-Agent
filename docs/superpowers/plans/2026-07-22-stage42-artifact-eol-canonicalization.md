# Stage 42 Artifact EOL Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Stage 42 artifact audit treat LF and Git-expanded CRLF representations of committed JSON and Markdown evidence as equivalent without changing the historical manifest.

**Architecture:** Keep the existing whitelist, privacy scan, and manifest comparison flow. Add one byte-level helper used only by inventory hashing: `.json` and `.md` bytes replace CRLF with LF, while every other suffix remains byte-for-byte strict.

**Tech Stack:** Python 3.11, `pathlib`, `hashlib`, pytest, existing Stage 42 artifact auditor.

---

### Task 1: Canonicalize Text Artifact Inventory Hashes

**Files:**
- Modify: `tests/test_stage42_artifact_audit.py`
- Modify: `scripts/audit_stage42_artifacts.py`

- [ ] **Step 1: Add a failing CRLF equivalence regression test**

Append this test to `tests/test_stage42_artifact_audit.py`:

```python
def test_audit_accepts_git_expanded_crlf_for_json_and_markdown(tmp_path):
    run = _make_run(tmp_path)
    manifest = write_artifact_manifest(run, run_id="stage42-run")

    for relative_path in (
        "metrics.json",
        "report.md",
        "retrieval-cases/redis.json",
    ):
        path = run / relative_path
        lf_bytes = path.read_bytes()
        assert b"\r\n" not in lf_bytes
        path.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    assert audit_release_artifacts(run, expected_run_id="stage42-run") == manifest
```

- [ ] **Step 2: Run the new test and verify the raw-byte audit fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage42_artifact_audit.py::test_audit_accepts_git_expanded_crlf_for_json_and_markdown -q
```

Expected: FAIL with `ArtifactAuditError: artifact manifest mismatch`.

- [ ] **Step 3: Implement suffix-limited byte canonicalization**

Replace the raw streaming hash helper in `scripts/audit_stage42_artifacts.py` with helpers that derive size and SHA-256 from the same selected bytes:

```python
CANONICAL_TEXT_SUFFIXES = {".json", ".md"}


def _inventory_bytes(path: Path) -> bytes:
    content = path.read_bytes()
    if path.suffix.casefold() in CANONICAL_TEXT_SUFFIXES:
        return content.replace(b"\r\n", b"\n")
    return content


def _artifact_record(path: Path, *, run_dir: Path) -> dict:
    content = _inventory_bytes(path)
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
```

Update `_inventory()` to build artifacts with:

```python
artifacts = [_artifact_record(path, run_dir=run_dir) for path in files]
```

Do not change `_scan_sensitive_content()`: it must continue scanning original UTF-8 file text. Do not normalize lone carriage returns, Unicode, PNG files, or other suffixes.

- [ ] **Step 4: Run focused tests and the saved Stage 42 audit**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage42_artifact_audit.py tests/test_stage44a_artifact_audit.py -q
& 'F:\python3.11\python.exe' -m scripts.audit_stage42_artifacts --run-dir reports/stage42-acceptance/20260716T062331Z-real-model-rc --run-id 20260716T062331Z-real-model-rc
```

Expected: both test modules PASS and the saved 30-case Stage 42 artifact inventory audits successfully without rewriting `manifest.json`.

- [ ] **Step 5: Run Stage 44A and complete regression gates**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
$env:STAGE41_PYTHON = 'F:\python3.11\python.exe'
npm.cmd run test:browser
& 'F:\python3.11\python.exe' -m scripts.audit_stage44a_artifacts --run-dir reports/stage44a-acceptance/20260722T054127Z-stage44a-bge-m3 --run-id 20260722T054127Z-stage44a-bge-m3
git diff --check
```

Expected: Python, browser, Stage 44A privacy, and whitespace gates PASS. If Windows Playwright server ownership hangs, run the same browser projects with the existing ignored `tmp/playwright-stage44a.config.js` and a controlled uvicorn process.

- [ ] **Step 6: Commit the focused defect fix**

```powershell
git add scripts/audit_stage42_artifacts.py tests/test_stage42_artifact_audit.py
git commit -m "fix: canonicalize stage 42 artifact line endings"
```

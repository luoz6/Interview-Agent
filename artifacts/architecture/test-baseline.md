# Test Baseline

> 任务：P0A-T06
> 状态：PASS（已记录环境限制与失败事实，未伪造通过）

## Environment

- Python: 3.11（系统解释器）
- Working directory: `F:\agent\Interview-Agent`
- PostgreSQL: 不可用；`127.0.0.1:5432` connection refused
- 关键 warning：
  - `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`
  - 多个 `PydanticJsonSchemaWarning` 来自 A2A agent card default value 序列化

## Commands and Results

### 1. `python -m pytest --collect-only -q`

- Result: `4206 tests collected in 6.32s`
- `collected`: 4206
- `passed`: N/A（collect-only）
- `failed`: N/A
- `skipped`: N/A
- `xfail`: N/A
- `errors`: N/A
- `duration`: 6.32s

### 2. `python -m pytest tests/architecture -q`

- Result: `2 failed, 79 passed, 4 warnings in 14.89s`
- `collected`: 81
- `passed`: 79
- `failed`: 2
- `skipped`: 0
- `xfail`: 0
- `errors`: 0
- `duration`: 14.89s

Failures:

1. `tests/architecture/test_api_router.py::test_composed_openapi_has_expected_unique_operation_inventory`
   - Expected: `len(schema["paths"]) == 68`
   - Actual: `116`
2. `tests/architecture/test_current_document_paths.py::test_current_document_script_modules_and_test_paths_exist`
   - Expected: `findings == []`
   - Actual: 25 missing referenced paths, first:
     `docs/adr/user-materials-rag-v1.md: missing test path tests/acceptance/test_memory_operational_shadow_acceptance.py`

### 3. `python -m pytest tests/contracts -q`

- Result: `836 passed, 2 skipped, 4 errors, 5 warnings in 227.54s`
- `collected`: 842
- `passed`: 836
- `failed`: 0
- `skipped`: 2
- `xfail`: 0
- `errors`: 4
- `duration`: 227.54s (0:03:47)

Errors:

1. `tests/contracts/test_owned_postgres_scope_postgres.py::test_real_target_ownership_cleanup_and_zero_residue`
2. `tests/contracts/test_owned_postgres_scope_postgres.py::test_real_wrong_target_is_rejected_before_scope_creation`
3. `tests/contracts/test_owned_postgres_scope_postgres.py::test_real_permission_denied_is_stable_and_leaves_no_scope`
4. `tests/contracts/test_owned_postgres_scope_postgres.py::test_stage43b_cli_binds_real_cleanup_receipt_to_evidence`

All four are setup errors caused by:

```text
connection to server at "127.0.0.1", port 5432 failed: Connection refused
configured POSTGRES_DSN is unreachable
```

## Aggregate Observed Facts

- `collect-only collected`: 4206
- `architecture suite`: 79 passed / 2 failed / 0 errors
- `contracts suite`: 836 passed / 0 failed / 2 skipped / 4 errors
- No `xfail` observed in the executed subsets.

## Environment Limitations

- Full `pytest` run was not executed; this task followed the required three commands only.
- PostgreSQL-dependent contract tests cannot be validated in the current environment.
- FastAPI/Pydantic deprecation and JSON-schema warnings are non-blocking but should be tracked.

## Important

These failures/errors are recorded as pre-refactor baseline facts. This task did not attempt to fix them.

# Local V1 Hardening execution baseline

## Repository

```text
EXECUTION_START_HEAD=2b8cde040fb554288839b46e0cc95a15e42adab3
EXECUTION_REMOTE_HEAD=2b8cde040fb554288839b46e0cc95a15e42adab3
AHEAD=0
BEHIND=0
BRANCH=codex/local-v1-hardening
WORKTREE=isolated
ISOLATED_WORKTREE_DIRTY_PATHS=0
MAIN_USER_OWNED_PATHS=14
```

The main worktree paths are pre-existing user work. They are outside this task and must not be staged, overwritten, restored or cleaned.

## Plan lineage

```text
DOWNLOADED_V03_SHA256=651486b57242989290d5acf18b6abf1d1c48270d2f4b49fb5a8469fc485d084a
DOWNLOADED_V04_DETAILED_SHA256=3e84f558848856b36933a5531ea55226050aecf5f8071d3735ec15274242b4fb
INHERITED_HOSTED_PLAN_GIT_BLOB=48cd14ce4e4841b5fa740d3629c574aae04e68d5
INHERITED_HOSTED_PLAN_STATE=FROZEN_NON_EXECUTABLE
```

## Toolchain

```text
OS=Windows
PYTHON=3.11.3
PIP=22.3.1
NODE=22.21.0
NPM=10.9.4
GIT=2.53.0.windows.3
```

Public documentation uses environment-independent executable names. Machine-specific executable locations, DSNs, credentials, ledger locations, Principal IDs, Session IDs and fact locators are not recorded here.

## Historical evidence boundary

The pre-hardening repository evidence reports 2123 Python/PostgreSQL tests passed with one authorized real-provider skip, 86 browser tests passed with 38 conditional skips, and production frontend build PASS. Those results remain historical evidence for the pre-hardening tree and do not prove the future hardening revision.

The downloaded v0.3 review also reported a separate `1941 passed / 182 skipped / 1 failed` run and a Linux hash-locked installation failure. Those are diagnostic inputs only until a structured command, environment and failed-node record is reproduced.

```text
LOCAL_HARDENING_BASELINE=PASS
LOCAL_HARDENING_IMPLEMENTATION=AUTHORIZED
LOCAL_MEMORY_DEFAULT=DISABLED
REAL_CANDIDATE_DATA=PROHIBITED
REAL_PROVIDER_EVALUATION=NOT_RUN
HOSTED_V2=NO_GO_FOR_NOW
```

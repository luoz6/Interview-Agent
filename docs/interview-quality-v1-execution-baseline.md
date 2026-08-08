# Interview Quality V1 execution baseline

## Authorization and scope

```text
PLAN_VERSION=v0.2.1
PLAN_STATUS=IN_EXECUTION
EXECUTION_AUTHORIZED=true
IMPLEMENTATION_STATUS=IN_PROGRESS
PROVIDER_AUTHORIZATION=GRANTED_UNIFIED_UNLIMITED
PROVIDER=DeepSeek_OPENAI_COMPATIBLE
PROVIDER_MODEL=deepseek-chat
PROVIDER_DATA_SCOPE=SYNTHETIC_PUBLIC_OR_REDACTED_ONLY
REAL_CANDIDATE_USE=PROHIBITED
HOSTED_V2=FROZEN_NON_EXECUTABLE
```

The Provider authorization does not permit real-candidate data, Hosted deployment,
automatic model substitution, Principal Memory use in scoring/reporting, or changes
outside the plan. API credentials are never recorded in this repository.

## Repository isolation

```text
MAIN_WORKTREE=F:/agent/Interview-Agent
EXECUTION_WORKTREE=F:/agent/Interview-Agent-quality-v1
EXECUTION_BRANCH=codex/interview-quality-v1
EXECUTION_START_HEAD=81ff57ce22842b2bc4fdb48274b4fa9952b29f0d
EXECUTION_START_TREE=21e49f57d70abde583f30052928d60cd4f3a16f4
ORIGIN_MASTER_AHEAD=0
ORIGIN_MASTER_BEHIND=0
EXECUTION_START_DIRTY_PATHS=0
MAIN_USER_OWNED_DIRTY_PATHS=14
```

The 14 paths in the main worktree are pre-existing user work. They are outside this
task and must not be staged, committed, overwritten, restored, moved, or merged into
the interview-quality branch without separate explicit authorization.

## Plan lineage

```text
PLAN_SOURCE=Interview-Agent-INTERVIEW_QUALITY_V1-plan-v0.2-detailed.md
PLAN_INTERNAL_VERSION=v0.2.1
PLAN_SHA256=3E513EA0BDA3F2116A956FB191ABBAA094F2DA0B38F6208CF3F00F887C1600FE
PLAN_BYTES=138547
```

The downloaded plan is external to the repository. Its hash above binds this
execution baseline to the user-authorized revision without copying machine-specific
paths or sensitive data into publication evidence.

## Initial toolchain observation

```text
OS=Microsoft Windows 11 Home
OS_VERSION=10.0.26200
ARCH=AMD64
POWERSHELL=5.1.26100.8115
PYTHON=3.11.3
PYTHON_PATH=F:/python3.11/python.exe
PIP=22.3.1
NODE=22.21.0
NPM=10.9.4
GIT=2.53.0.windows.3
DOCKER=29.4.2
POSTGRES_DSN_PRESENT=false
PSQL=NOT_FOUND
POSTGRES_SERVICE_COUNT=0
ROOT_PLAYWRIGHT_INSTALL=NOT_PRESENT_IN_NEW_WORKTREE
```

PostgreSQL 16 and the pinned Playwright Chromium remain T01 setup work. Their absence
is not a PASS and must not be converted into a non-blocking skip.

## Lockfile hashes

```text
requirements.lock.txt=67BDF21BB7390FFDB1F9DB3F6A20B84D8BE94062521796A2694B23D767B8C716
requirements-windows.lock.txt=67BDF21BB7390FFDB1F9DB3F6A20B84D8BE94062521796A2694B23D767B8C716
requirements-linux.lock.txt=9FDBEBDCF3C4A65A8181240FA18140D3F796BCB1D202435EBAA310D1C264C01B
requirements.lock.meta.json=872C38A0360787930564F9E70F5FD25E47C74B80F88F12049648845ACE62200E
package-lock.json=7310A26277A4B5C0C7F6EAD87147A35BFE59716121F2A43216DD7E16828A6468
frontend/package-lock.json=16C9395DE5F194D673686383F901A92874FB3770328702A9B479D2C75A05F29B
.python-version=E3CCF97AB9C37001E4A387554E972811BED0BF6A9C320ED309EC9E2671EDC64E
.node-version=AB936CA4B1309007A1752D69C3611E37B3E00154ADCE6F31B3FCF318B66469BB
```

## T00 result

```text
T00_ENGINEERING_STATUS=PASS
USER_WORK_PRESERVED=true
UNRECOGNIZED_FILES_INCLUDED=0
DETACHED_HEAD=false
```


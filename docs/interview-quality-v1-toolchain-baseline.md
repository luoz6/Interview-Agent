# Interview Quality V1 toolchain baseline

## Frozen Windows execution toolchain

```text
OS=Microsoft Windows 11 Home
OS_VERSION=10.0.26200
ARCH=AMD64
POWERSHELL=5.1.26100.8115

PYTHON_VERSION=3.11.3
PYTHON_EXECUTABLE=F:/python3.11/python.exe
PIP_VERSION=22.3.1

NODE_VERSION=22.21.0
NPM_VERSION=10.9.4
GIT_VERSION=2.53.0.windows.3

PLAYWRIGHT_VERSION=1.61.1
CHROMIUM_VERSION=149.0.7827.55
CHROMIUM_EXECUTABLE=C:/Users/admin/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe

POSTGRESQL_VERSION=16.14
PGVECTOR_VERSION=0.8.6
POSTGRES_CONTAINER=interview-quality-v1-pg16
POSTGRES_IMAGE=pgvector/pgvector@sha256:a36250871de0833b8757561c72f2477ef1ddd1101afa4e617fb552e0de514c6b
POSTGRES_HOST_BIND=127.0.0.1:55432

REPRODUCIBILITY_DEPENDENCY_INVENTORY_SHA256=d28f9ac6f61516ef4128362e87cdacde8d4466b1205556ace834d0da441c839a
```

The local PostgreSQL container uses trust authentication only on the loopback-bound
test port. It is an isolated test service and not a production deployment. The DSN is
provided to individual commands through process-local environment state and is not
written to publication manifests.

## Installation verification

```text
ROOT_NPM_CI=PASS
FRONTEND_NPM_CI=PASS
ROOT_NPM_AUDIT_VULNERABILITIES=0
FRONTEND_NPM_AUDIT_VULNERABILITIES=0
PLAYWRIGHT_CHROMIUM_LAUNCH=PASS
POSTGRES_READY=PASS
PGVECTOR_CREATE_EXTENSION=PASS
REPRODUCIBILITY_PREFLIGHT=PASS
```

`npm ci` did not modify either lockfile:

```text
package-lock.json=7310A26277A4B5C0C7F6EAD87147A35BFE59716121F2A43216DD7E16828A6468
frontend/package-lock.json=16C9395DE5F194D673686383F901A92874FB3770328702A9B479D2C75A05F29B
```

## Cross-platform boundary

T01 freezes the local Windows command paths above. Ubuntu 24.04 verification remains
a Gate 6 requirement and cannot be inferred from this Windows result.

## T01 result

```text
T01_ENGINEERING_STATUS=PASS
POSTGRES_BLOCKING_SKIP_ALLOWED=false
WRONG_PYTHON_INTERPRETER_ALLOWED=false
UNPINNED_BROWSER_ACCEPTANCE_ALLOWED=false
```


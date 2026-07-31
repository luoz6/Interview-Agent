# Production Budget Shadow external approval record contract

This reference defines the machine-readable approval record required before the
production Budget Shadow change preflight can pass. The record is owned by an
external change-management system. It is not created, approved, or signed by
the repository runner.

The repository example is never an approval. It is intentionally `PENDING`,
contains no deployment binding, and must fail the change preflight.

## Trust boundary

An approved record must:

- be exported from the trusted external change-management system to a path
  outside the repository workspace;
- have an expected record SHA-256 supplied through an independent trusted
  channel;
- bind one immutable Git revision;
- bind one deployment scope SHA-256 obtained through the approved deployment
  inventory, not from repository configuration;
- bind one change ticket digest, a traffic limit, start/end window, and expiry;
- contain five independent approvals: change owner, operations, privacy,
  security, and fairness;
- expire no later than the approved observation window;
- request `BUDGET_SHADOW_ONLY` and no other memory phase.

Copying the example, editing a JSON file by hand, computing both the record and
its expected digest from the same untrusted file, or placing an approved record
in the repository does not satisfy this boundary.

## Canonical record

The external record uses canonical JSON for integrity verification: UTF-8,
object keys sorted lexicographically, no insignificant whitespace, and compact
`,`/`:` separators. SHA-256 is computed over those canonical bytes.

```json
{
  "schema_version": "memory-production-shadow-approval-record-v1",
  "approval_status": "APPROVED",
  "requested_phase": "BUDGET_SHADOW_ONLY",
  "approved_revision": "<7-to-40 lowercase hexadecimal Git revision>",
  "deployment_scope_sha256": "<64 lowercase hexadecimal characters>",
  "traffic_percent": 1.0,
  "window_start": "<timezone-aware ISO-8601 timestamp>",
  "window_end": "<timezone-aware ISO-8601 timestamp>",
  "expires_at": "<timezone-aware ISO-8601 timestamp>",
  "change_ticket_sha256": "<64 lowercase hexadecimal characters>",
  "approvals": {
    "change_owner": {
      "decision": "APPROVED",
      "approver_ref_sha256": "<64 lowercase hexadecimal characters>",
      "decided_at": "<timezone-aware ISO-8601 timestamp>"
    },
    "operations": {
      "decision": "APPROVED",
      "approver_ref_sha256": "<64 lowercase hexadecimal characters>",
      "decided_at": "<timezone-aware ISO-8601 timestamp>"
    },
    "privacy": {
      "decision": "APPROVED",
      "approver_ref_sha256": "<64 lowercase hexadecimal characters>",
      "decided_at": "<timezone-aware ISO-8601 timestamp>"
    },
    "security": {
      "decision": "APPROVED",
      "approver_ref_sha256": "<64 lowercase hexadecimal characters>",
      "decided_at": "<timezone-aware ISO-8601 timestamp>"
    },
    "fairness": {
      "decision": "APPROVED",
      "approver_ref_sha256": "<64 lowercase hexadecimal characters>",
      "decided_at": "<timezone-aware ISO-8601 timestamp>"
    }
  }
}
```

Approver and ticket hashes are private change-system bindings. They must not be
copied into aggregate repository evidence or general logs.

## Field constraints

| Field | Constraint |
|---|---|
| `approval_status` | Exactly `APPROVED` for an executable window; the repository example remains `PENDING` |
| `requested_phase` | Exactly `BUDGET_SHADOW_ONLY` |
| `approved_revision` | Exact deployed revision; no branch, tag, or moving reference |
| `deployment_scope_sha256` | Exact match to the separately verified production target digest |
| `traffic_percent` | Greater than 0 and no greater than 1.0 |
| `window_start` / `window_end` | Timezone-aware; duration at least 24 and at most 168 hours |
| `expires_at` | Not in the past and not later than `window_end` |
| `approvals` | Exactly the five required roles; no missing, extra, pending, or rejected role |
| `decided_at` | Timezone-aware and no later than the approved window start |

The preflight must run inside the approved window. A valid record does not
extend itself, authorize a later window, authorize more traffic, or authorize
Principal Write/Read Shadow.

## Repository evidence boundary

The preflight output may record only booleans, aggregate counts, the requested
phase, traffic percentage, window duration, and stable gate codes. It must not
record:

- the external record or its path;
- approver, ticket, deployment, or record digests;
- production connections, credentials, database identifiers, or table names;
- candidate/session/principal/fact/question locators;
- Prompt, answer, resume, report, source, or Provider payload.

The repository-provided example is:

`docs/memory-production-shadow-approval-record.example.json`

Its required result is:

```text
APPROVAL_RECORD_NOT_EXTERNAL
APPROVAL_STATUS_NOT_APPROVED
```

## Authorization boundary

A PASS result verifies that the external record authorizes entering the exact
scheduled change window. The validator itself does not change configuration,
start a deployment, or mark production observation as completed. Principal
Write Shadow, Principal Read Shadow, and long-term-memory consumption remain
outside the record's authority.

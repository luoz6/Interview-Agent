# Local V1 hardening closure and Hosted V2 frozen handoff

## Handoff state

```text
LOCAL_V1_IMPLEMENTATION=FEATURE_COMPLETE
LOCAL_V1_HARDENING=COMPLETE
LOCAL_V1_FINAL_ACCEPTANCE=PASS
LOCAL_V1_DEFAULT=DISABLED
LOCAL_V1_REAL_CANDIDATE_USE=PROHIBITED
REAL_PROVIDER_EVALUATION=NOT_RUN
NEXT_REQUIRED_TASK=NONE
OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION
HOSTED_V2=NO_GO_FOR_NOW
INHERITED_PLAN_EXECUTION_STATE=FROZEN_NON_EXECUTABLE
HOSTED_V2_HANDOFF=RETAINED_NO_GO
```

Local V1 has no remaining required implementation or hardening task under the
approved v0.4 scope. This is a closure, not authorization to broaden the
product boundary.

## Accepted implementation and publication model

The immutable implementation accepted by H7 is:

```text
revision=e6b8f29d25276f17c874d07cebc15565bad37492
tree=354d3d0a1ad99bfef57fd51244d1f5358442c79f
```

H8 uses a separate documentation-only publication revision. The tracked
manifest deliberately does not contain that revision's own identity. The
external verification anchor is:

```text
refs/tags/local-v1-hardening-v0.4-accepted
```

Reviewers should verify that the tag resolves to the publication commit, that
the branch contains that commit, and that the implementation revision is in
its parent history.

## Local V1 operating boundary

Local V1 is trusted-local, single-user and default-off. It supports explicit
local user rights, current Consent checks, safe exports, deletion, protected
tombstone replay, bounded follow-up-only assistance, aggregate telemetry and a
Memory Center. It has no hosted account, tenancy, recovery or multi-user
authorization boundary.

The implementation may change a follow-up when Local Consume is explicitly
enabled by a trusted local operator. A changed follow-up may change later
answers. The absence of direct Principal Memory imports in score and report
modules does not prove equal outcomes or fairness.

## What must remain frozen

- Committed long-term-memory defaults remain disabled.
- A model proposal never becomes active automatically.
- Read Shadow remains zero-write and zero-injection.
- Disabled mode remains zero activity, not merely zero injection.
- Exclusive facts remain database-constrained.
- The protected ledger and replay watermark remain readiness requirements.
- Local facts, Consent records and tombstones must not automatically migrate to
  a future hosted system.
- Scoring, reports, recommendations and hiring decisions remain outside the
  Principal Memory consumption boundary.

## Safe operations and rollback

Use `docs/local-v1-runbook.md` and
`docs/local-principal-memory-operations.md` for local operation. Preflight must
fail closed when the configured mode, gates, database schema, current Consent,
protected ledger, replay watermark, metrics or host-native path contract is not
valid.

Rollback disables the mode and all capability gates while preserving the
ledger, tombstones and migrations. Do not delete durable safety records as a
rollback shortcut.

## Hosted V2 retained no-go

The complete Hosted V2 roadmap remains embedded as the frozen Part II of the
v0.4 detailed plan. Its source SHA-256 remains
`de0afe41e815b8befbd56ae4acdd5ed7e07540a0baffd3d06bdca4e6542c3227`.

Future Hosted V2 work is optional and must begin from a new baseline and a
fresh decision. Before any implementation, the new task must:

1. recapture repository, identity, Consent, Provider, data-use and deployment
   baselines;
2. approve or formally replace the Productization ADR;
3. approve a hosted data-use specification and Provider policy;
4. define real account recovery and cross-user isolation;
5. define candidate-visible controls and no-dark-pattern behavior;
6. authorize a new implementation plan and a separate production observation
   plan.

No Local V1 PASS state may be reused as Hosted V2, production Shadow, canary,
fairness or real-candidate evidence.

## Final reviewer checklist

- Confirm acceptance and manifest use the frozen implementation revision/tree.
- Confirm publication changes are limited to the approved documentation,
  evidence, plan-status and contract-test paths.
- Confirm external raw artifacts are represented only by SHA-256 digests.
- Confirm all 81 skips are classified and blocker count is zero.
- Confirm PostgreSQL, port and process residue are zero.
- Confirm the annotated publication tag is immutable and remotely visible.
- Confirm the main user worktree's 14 pre-existing entries remain untouched.
- Confirm Hosted V2 and all real-candidate/real-Provider claims remain no-go or
  not run.

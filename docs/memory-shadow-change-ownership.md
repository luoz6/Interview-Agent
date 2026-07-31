# Memory Shadow Change Ownership

Status: Task 0 ownership audit in progress. This record classifies paths; it does
not authorize broad staging, discard user work, enable Shadow, or deploy a
Release Candidate.

## Baseline

- Repository base revision at audit start: `9132cf3`.
- Worktree: heavily dirty, containing memory work, React/browser migration,
  user design assets, repository hygiene changes, and six staged retired-HTML
  deletions.
- Existing staged paths are limited to `app/test0.html` through
  `app/test4.html` and `app/test-help.html`. Those deletions are expected and
  must not be restored or casually unstaged.
- The source of truth for classification is
  `scripts/memory_shadow_release_preflight.py`.

## Ownership policies

| Policy | Meaning | Commit behavior |
|---|---|---|
| `include` | Clearly owned by the memory optimization/foundation phases | Eligible for a scoped, dependency-coherent commit |
| `shared_review` | React/browser or central integration overlaps workstreams | Review as a coherent group before staging |
| `exclude` | Existing user design work outside the memory RC | Preserve in the worktree; do not stage in memory commits |
| `manual_review` | Repository-level or unknown ownership | Blocks RC until explicitly classified |

## Clearly included domains

- Structured memory configuration and safe defaults.
- Context budget, compression eligibility, artifact and selection changes.
- Question Memory contracts, indexes, retrieval and recovery.
- Session deletion, tombstones, replay and fault injection.
- Durable aggregate memory metrics.
- Knowledge P1 corpus, manifest, retrieval fixtures and coverage tests.
- Principal Identity, Consent, Fact Store, proposal, lifecycle, deletion,
  bounded read-shadow, privacy and Knowledge Firewall.
- Runtime/migration/graph/provider/trace integration required by those domains.
- Memory specifications, plans, runbooks, threat model, acceptance evidence and
  acceptance tools.

## Shared review domains

The following are required to keep the accepted React/browser and API contract
coherent, but contain cross-workstream integration and must be reviewed as a
group:

- `frontend/**`;
- `app/api/routes.py` and `app/main.py`;
- `app/static/*.js`, excluding the separately classified prototype CSS;
- `package.json`, `package-lock.json`, `playwright.config.js` and
  `scripts/run_browser_tests.js`;
- browser test specifications and `tests/browser_support_app.py`;
- README, local runbook, frontend/interface documentation;
- central Session/PostgreSQL Session files and broad API/page/report tests;
- the six retired historical HTML deletions.

The shared group cannot be split solely by filename if doing so makes the API,
React routes or browser suite non-functional. It must pass compile/build,
focused API tests and the complete browser suite as one dependency-coherent
revision.

## Explicitly excluded user work

- `.hallmark/**`;
- `DESIGN.md`;
- `app/static/prototype-source.css`;
- `app/static/prototype.css`.
- `frontend/src/styles/reports-app.css`, created after the isolated RC candidate
  was assembled and therefore outside the validated candidate boundary.

These paths contain design-analysis or large visual-system changes and are not
owned by the memory RC. They remain untouched in the worktree.

## Reviewed repository-hygiene exclusion

- `.gitattributes` contains one line that fixes LF only for
  `docs/prototypes/interview-agent-single-file.html`. That prototype artifact is
  outside the memory RC, so the file is explicitly excluded rather than left
  as unresolved manual review.

Any future path without a classification rule is `manual_review` and blocks the
preflight. The tool fails closed rather than guessing ownership.

## Sensitive-path policy

The preflight rejects untracked or changed paths that look like credentials,
private keys, local databases or non-example environment files. It inspects
paths only; it does not print file contents, credentials, DSNs, Prompt data or
candidate data.

## Commit topology rule

The seven functional commit names in the operational plan are guidance, not a
mechanical sequence. Every actual revision must compile and pass its affected
focused tests. Shared factories, config contracts and migration registries may
be grouped with their minimum direct dependants to avoid an invalid
intermediate revision.

## Task 0 exit conditions

- The real preflight has no unclassified or sensitive paths.
- Only the retired historical HTML deletions may already be staged.
- Every included/shared file has a commit group.
- Excluded user paths remain unstaged and unmodified by Task 0.
- The proposed RC topology is compile/test coherent.
- No Shadow mode or trusted-local API is enabled.

## Current audit snapshot

- Changed paths: 264.
- Clearly included: 188.
- Shared review: 69.
- Explicitly excluded: 7.
- Existing staged paths: 6 expected retired-HTML deletions.
- Unclassified/sensitive blockers: 0.
- Isolated candidate compile: passed.
- Isolated candidate focused suite: 359 passed, 12 skipped.
- Isolated candidate frontend build: passed, 4587 modules transformed.
- Isolated candidate full browser: 54 passed, 22 configured skips.

The browser count differs from the previous foundation evidence because the
candidate contains the current 76-test browser project. It is a complete-suite
result, not a partial selection.

After candidate validation, a concurrent user edit changed
`frontend/src/pages/ReportsPage.jsx` and created
`frontend/src/styles/reports-app.css`. The RC index retains the validated
  candidate version of `ReportsPage.jsx`; the newer worktree version and CSS are
  preserved outside the RC for the user.

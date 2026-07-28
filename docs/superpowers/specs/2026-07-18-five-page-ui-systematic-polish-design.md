# Five-Page UI Systematic Polish Design

Status: `PENDING_USER_REVIEW`

Date: 2026-07-18

## 1. Context

The five-page web experience is functionally complete and already uses real
application data:

- `test4.html`: interview preparation
- `test3.html`: live mock interview
- `test2.html`: report processing
- `test1.html`: report detail
- `test0.html`: report center

The current implementation has passed API, privacy, PostgreSQL, browser, and
full Python acceptance. This follow-up does not redesign the product workflow.
It improves the visual hierarchy, page proportions, information density, and
interaction feedback while preserving the proven runtime contracts.

The approved direction is:

- **Aesthetic:** precision workbench
- **Density:** adaptive by page
- **Navigation:** shared top bar with page-specific contextual sidebars
- **Polish scope:** balanced visual hierarchy, layout, and interactions
- **Visual target:** desktop web at 1280x800 and 1440x1000

## 2. Goals

1. Make all five pages read as one mature professional product.
2. Strengthen hierarchy without introducing marketing composition or
   decorative visual noise.
3. Match density to the task performed on each page.
4. Standardize commands, states, focus treatment, notices, loading states, and
   motion.
5. Preserve all real API bindings, privacy boundaries, and recovery behavior.
6. Reduce CSS duplication and prevent further stylesheet growth.

## 3. Non-Goals

- No frontend framework migration.
- No backend API, PostgreSQL schema, event, or report-model changes.
- No workflow rewrite or merging of the five independent HTML pages.
- No new demo values, simulated progress, or static report content.
- No mobile visual redesign. Existing mobile behavior and browser regression
  coverage remain in place to prevent accidental breakage.
- No external CDN, webfont, icon runtime, or new production dependency.
- No gradient, large-radius, glassmorphism, or marketing-style treatment.

## 4. Architecture Boundary

The production architecture remains:

```text
Independent HTML routes
        |
        v
Page-specific ES modules
        |
        +--> shared-ui.js
        +--> api.js
        |
        v
Existing FastAPI endpoints
```

The refactor operates only in:

- `app/test0.html` through `app/test4.html`
- `app/test-help.html` when shared shell alignment requires it
- `app/static/prototype-source.css`
- the generated `app/static/prototype.css`
- existing page JavaScript only when an interaction state requires a class,
  attribute, or stable rendering hook

All existing DOM IDs, query parameters, storage keys, API URLs, SSE handling,
PDF download behavior, report requeue behavior, and public runtime-trace
allowlists remain unchanged.

## 5. Visual System

### 5.1 Color Tokens

The interface uses a neutral canvas, a dark ink hierarchy, one command color,
and independent semantic colors:

| Role | Target | Use |
| --- | --- | --- |
| Ink | `#172033` | Primary text and strong headings |
| Muted ink | `#647084` | Supporting text and metadata |
| Canvas | `#F4F6F9` | Page background |
| Surface | `#FFFFFF` | Tools, tables, and repeated items |
| Line | `#DCE2EA` | Structural borders |
| Cobalt | `#2457D6` | Primary commands, selection, key progress |
| Success | `#17845E` | Completed and healthy states |
| Warning | `#B7791F` | Processing and recoverable attention |
| Danger | `#C2413B` | Failure and destructive commands |

Cobalt must not recolor entire page regions. Semantic states remain visibly
distinct so the UI does not become a one-note blue palette.

This table defines the target state, not the current implementation. The
existing stylesheet and static tests still encode the previous palette. The
implementation migrates them together according to this explicit map:

| Existing token | Existing value | Target value |
| --- | --- | --- |
| `--color-text` | `#1F2937` | `#172033` |
| `--color-muted` | `#64748B` | `#647084` |
| `--color-page` | `#F8FAFC` | `#F4F6F9` |
| `--color-surface` | `#FFFFFF` | `#FFFFFF` |
| `--color-line` | `#E2E8F0` | `#DCE2EA` |
| `--color-primary` | `#2563EB` | `#2457D6` |
| `--color-primary-hover` | `#1D4ED8` | `#1D47B3` |
| `--color-success` | `#16A34A` | `#17845E` |
| `--color-warning` | `#D97706` | `#B7791F` |
| `--color-danger` | `#DC2626` | `#C2413B` |

Repeated literal colors such as `#BFDBFE`, `#EFF6FF`, `#DBEAFE`, `#0F172A`,
and `#CBD5E1` must be classified by semantic role and replaced with shared
subtle-background, focus-ring, strong-ink, or control-border tokens. The
implementation must not perform an unreviewed global string replacement.
Static token assertions are updated in the same change as the source tokens.

### 5.2 Typography

The existing local system-font stack remains for performance and Chinese
coverage. The hierarchy is normalized:

- Page title: 24px, line-height 1.3
- Section title: 15px
- Compact panel title: 13px
- Body: 13-14px, line-height 1.55
- Metadata: 11-12px
- Scores and durations: tabular numeric rendering

Letter spacing remains zero. Hero-scale typography is not used inside tools,
sidebars, tables, or compact panels.

### 5.3 Shape and Elevation

- Card radius: 8px
- Control radius: 6px
- Pills are reserved for statuses and compact filters
- Structural grouping relies on borders
- Base cards and structural panels use borders without a default shadow
- One low-elevation variant is reserved for floating tools and active surfaces
- No nested decorative cards

The shared `.ui-card` contract therefore becomes flat. An explicit elevated
variant, rather than page-specific shadows, is required wherever elevation is
semantically justified. Hover must not add elevation to non-interactive
content.

## 6. Shared Product Shell

### 6.1 Top Bar

Every production page keeps the same:

- 60-64px stable height
- brand placement and mark
- real `/prep`, `/reports`, and `/help` navigation
- page-aware `aria-current`
- compact runtime or workspace status at the right

The active navigation state uses text weight and a two-pixel underline. It
does not use a filled rounded tab.

### 6.2 Contextual Sidebars

The approved navigation model keeps page-specific contextual sidebars:

- preparation and processing: four-step workflow
- interview: question plan
- report detail: report sections
- report center: status-filter controls

Navigation sidebars on preparation, processing, interview, and report detail
share:

- one desktop width range
- label, heading, count, and active-state rules
- a three-pixel cobalt active spine
- stable row heights and number markers
- consistent border and canvas treatment

The report-center status rail is a control group rather than navigation. It
shares width, typography, spacing, and semantic color tokens, but retains
`aria-pressed` and a selected-control treatment instead of the active spine.
It must not expose `aria-current`.

The report-detail section navigation tracks the section nearest the top of the
reading viewport. An `IntersectionObserver` maintains exactly one
`aria-current="location"` link. Anchor activation updates the URL hash, and a
hash-based fallback selects the matching link when observation is unavailable.
This behavior is progressive enhancement: report content and anchor navigation
remain usable if the observer cannot start.

The sidebars do not become a second global application navigation layer.

## 7. Adaptive Page Density

### 7.1 Preparation: Spacious

- Large text sources retain comfortable reading height.
- Upload controls and metadata align without increasing field height.
- Draft actions are secondary to plan generation.
- The plan summary remains visible as the decision surface.
- Generated topics and evidence use progressive disclosure below the main
  plan summary.

### 7.2 Live Interview: Dense

- Question navigation, current question, conversation, answer editor, and
  status rail remain visible within a desktop viewport.
- Message labels and avatars become visually quieter than message content.
- The answer editor uses stable dimensions and does not move when draft or
  error text changes.
- Focus mode continues to remove the two side columns without shifting the
  answer editor unexpectedly.

### 7.3 Report Processing: Balanced

- Real percentage and current stage dominate the first viewport.
- Timeline, metrics, retrieval summary, and job identity are secondary.
- Failed state replaces processing emphasis without claiming infrastructure
  health.
- Background navigation remains available in processing and failed states.

### 7.4 Report Detail: Spacious

- Score and summary stay compact enough to reveal the first analysis section.
- Dimension scores use aligned bars and stable numeric columns.
- Long feedback, evidence, improvements, and runtime trace preserve reading
  rhythm.
- Runtime trace remains visually secondary and independently recoverable.

### 7.5 Report Center: Dense

- Overview, status filters, search, date filter, table, and pagination form
  one scanning workflow.
- Table rows use stable heights and aligned numeric columns.
- Row actions wrap inside their cell without resizing other rows.
- Empty and filtered-empty states occupy the table surface rather than
  creating an unrelated page card.

## 8. Component System

### 8.1 Commands

- Primary: one per local action group
- Secondary: neutral border
- Destructive: danger border and text, not a solid red default
- Icon-only buttons are limited to familiar tools such as refresh
- Text or icon-plus-text remains valid for explicit workflow commands

All buttons have stable minimum height, visible focus, disabled treatment, and
no content-driven layout shift.

The shared destructive variant is `.ui-button-danger`. Its default state uses
danger text and border on a neutral surface; hover uses a subtle danger-tinted
surface, and focus remains clearly visible. It is applied to genuinely
destructive commands such as ending an interview, not to recoverable actions
such as retry or report requeue.

### 8.2 Inputs

- Labels, help text, metadata, and controls align to a shared field header
- Focus uses a border and subtle outer ring
- Validation and file-import failures render next to the source action
- Draft status never overlays the character count

### 8.3 Status and Feedback

- Status pills use the semantic palette
- Notices use a narrow semantic edge rather than a filled banner
- Success messages render after data refresh completes
- Errors render near the initiating command
- Loading placeholders reserve final component dimensions
- Report progress never invents intermediate values

## 9. Motion and Interaction

- `--motion-fast: 160ms` covers hover, focus, and press feedback
- `--motion-state: 200ms` covers selected, expanded, and status transitions
- A shared non-bouncy easing curve is used for both tokens
- Entry motion is restricted to one subtle page-shell reveal
- No continuous decorative animation
- Progress motion reflects real progress updates only
- `prefers-reduced-motion` disables nonessential transitions

Motion must not move controls, alter layout dimensions, or obscure state
changes.

Motion is applied only to interactive color, border, opacity, and real progress
changes. Message content and error notices do not animate merely to satisfy a
timing target. Under `prefers-reduced-motion: reduce`, page entry and transform
effects are disabled and state/progress updates become effectively immediate.

## 10. Data, Privacy, and Error Boundaries

### 10.1 Data Rules

- All visible business values remain API-driven.
- Dynamic strings continue to use `textContent` or `createEl`.
- `safe_metadata`, `payload_json`, private answers, raw resume text, and raw
  job descriptions are not added to public runtime rendering.
- Canonical report paths remain allowlisted.

### 10.2 Recovery Rules

- Network and SSE failures preserve the answer draft.
- A 409 state conflict refreshes the session while retaining unsent input.
- Report requeue errors retain filters, query, date range, and page.
- Runtime trace failure does not block report content.
- Loading, empty, failed, and completed states keep stable component geometry.

## 11. Accessibility

- Keyboard focus is visible on every command and link.
- Existing `aria-pressed`, `aria-current`, and `aria-live` contracts remain.
- Report-section scroll tracking maintains exactly one
  `aria-current="location"`; workflow progress continues to use
  `aria-current="step"`, and global page navigation uses
  `aria-current="page"`.
- Live regions announce only meaningful status changes.
- Color is never the only status signal.
- Text and controls meet desktop contrast requirements.
- Fixed tools and long labels are tested for clipping and overlap.

## 12. Responsive Scope

This design is visually optimized and accepted at:

- 1280x800
- 1440x1000

The existing mobile project and functional tests remain. The implementation
must not intentionally break mobile behavior, but it does not add a new mobile
composition or mobile screenshot acceptance target.

## 13. CSS Consolidation

Before deleting or merging a selector, the implementation must search all
production HTML and JavaScript consumers.

The stylesheet work must:

1. establish the shared token and shell layers;
2. consolidate repeated page-heading, panel-header, status, table, and action
   patterns;
3. preserve literal page selectors required by static contracts;
4. remove only proven unreferenced rules;
5. rebuild the generated CSS after each coherent batch.

This is a controlled consolidation, not an unrestricted stylesheet rewrite.

## 14. Testing and Acceptance

Required gates:

1. Tailwind CSS build
2. `node --check` for every file under `app/static/*.js`
3. existing static DOM and route contracts
4. UTF-8 and privacy contracts
5. page-specific JavaScript behavior tests
6. deterministic Playwright flows
7. screenshots and geometry checks at 1280x800 and 1440x1000
8. existing mobile Local V1 browser regression
9. complete Python regression
10. `git diff --check`

The static and browser suites add explicit contracts for the target color
tokens, `.ui-button-danger`, flat base cards, the two motion-duration tokens,
the reduced-motion override, and report-section `aria-current` tracking. Exact
animation frame timing is not asserted; tests verify tokens, reduced-motion
behavior, and stable interaction state instead.

The existing Playwright geometry helper already checks that document width does
not exceed viewport width and that wide report tables remain inside their local
scroll container at 1280x800. This remains a regression gate, not a known
overflow defect.

The implementation does not require a new PostgreSQL schema gate because it
does not modify persistence. If a database-backed regression is run, it must
use an isolated test database and never `ragent`.

No local model is downloaded or loaded, and no new container is created.

## 15. Success Criteria

The optimization is complete when:

- the five pages share one recognizable product shell;
- page density matches the approved map;
- the first viewport exposes the primary task and next command;
- controls and dynamic status changes do not shift layout;
- no production demo value or privacy regression is introduced;
- the two desktop screenshot targets have no clipping, overlap, or horizontal
  document overflow;
- all required gates pass;
- existing API and workflow behavior remains unchanged.

## 16. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| CSS growth from more page-specific rules | Consolidate shared patterns before adding variants |
| DOM hook regression | Preserve IDs and run static/browser contracts after each page |
| Excessive density in long reports | Keep report detail in the spacious density mode |
| Status color inconsistency | Route all statuses through semantic tokens |
| Interaction feedback overwrites business state | Render feedback after successful refresh and preserve view state |
| Desktop work breaks mobile behavior | Keep existing mobile browser regression in the release gate |
| Palette migration leaves literal legacy colors | Classify each literal by semantic role and update source and assertions together |
| Active-state semantics become inconsistent | Use `aria-current` only for navigation and `aria-pressed` only for filter controls |
| Motion harms reduced-motion users | Verify the reduced-motion override in static and browser tests |

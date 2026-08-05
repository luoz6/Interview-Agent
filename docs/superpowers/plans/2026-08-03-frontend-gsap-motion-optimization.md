# Frontend GSAP Motion Optimization Plan

**Date:** 2026-08-03  
**Status:** Proposed - analysis complete, implementation not started  
**Scope:** `frontend/` only  
**Design authority:** `DESIGN.md`  
**Primary routes:** `/prep`, `/interview`, `/report-processing`, `/report-detail`, `/reports`, `/help`

## 1. Executive decision

This project should not be converted into a site-wide GSAP animation system.

GSAP should be introduced as a narrowly scoped orchestration layer for motion that has at least one of the following properties:

- multiple elements must move in a coordinated sequence;
- an animation must be interrupted, retargeted, reversed, or killed;
- a value changes dynamically at runtime, such as report progress or a score;
- React state updates currently remount nodes only to replay CSS animation;
- responsive and reduced-motion behavior requires explicit lifecycle cleanup.

Based on the current frontend implementation, the highest-value GSAP targets are:

1. `/report-processing`: continuously retargeted progress, semantic stage transitions, and newly appended events;
2. `/report-detail`: score counting and the coordinated report-overview reveal;
3. `/interview`: an optional focus-mode layout transition after the first two targets are stable.

The following routes should remain primarily CSS-driven:

- `/prep`;
- `/reports`;
- `/help`.

The first implementation step must be route-level code splitting. GSAP must not be added to the current monolithic route bundle and shipped to pages that do not use it.

## 2. Current-state evidence

### 2.1 Frontend stack

The frontend is an independent Vite/React application using:

- React 19;
- Vite 6;
- Phosphor Icons;
- route-specific CSS files;
- no animation library at present;
- no current `gsap` or `@gsap/react` dependency.

### 2.2 Route loading

`frontend/src/App.jsx` statically imports every page and then selects a component through `window.location.pathname`.

Consequences:

- all route modules participate in the initial dependency graph;
- route CSS imported from those modules is not cleanly isolated from the main application load;
- introducing GSAP in one statically imported page risks adding it to the shared initial bundle;
- users opening `/prep` can pay for report-detail and report-processing code they do not need.

The most recently recorded production-build baseline was approximately:

- CSS: 263,650 bytes;
- JavaScript: 476,075 bytes;
- JavaScript source map: 1,475,732 bytes.

These values must be regenerated before implementation and compared again after route splitting and after GSAP is introduced.

### 2.3 Existing motion surface

The inspected stylesheets contain approximately 41 `@keyframes` declarations and at least 64 explicit `animation` declarations.

| Stylesheet | Keyframes | Explicit animation declarations | Approximate source size |
|---|---:|---:|---:|
| `styles/report-detail-app.css` | 13 | 13 | 50.9 KB |
| `styles/report-processing-app.css` | 8 | 12 | 25.4 KB |
| `styles/interview-app.css` | 1 | 11 | 36.9 KB |
| `styles/start-app.css` | 3 | 8 | 45.3 KB |
| `styles/start-page.css` | 4 | 5 | 31.3 KB |
| `styles/help-app.css` | 5 | 8 | 18.2 KB |
| `styles/reports-app.css` | 3 | 7 | 32.7 KB |
| `styles/index.css` | 4 | global/legacy rules | 54.6 KB |

Motion ownership is currently distributed across:

- CSS transitions;
- CSS keyframes;
- React `key` changes that remount nodes and replay animations;
- `IntersectionObserver` callbacks;
- manual `requestAnimationFrame` loops;
- native smooth scrolling;
- timeout-driven temporary states.

This fragmentation is the main motion-system problem. The goal is not to increase the number of animated elements; the goal is to give complex transitions one owner and one lifecycle.

### 2.4 Known duplication

The global keyframe name `start-spin` is defined in both:

- `frontend/src/styles/start-app.css`;
- `frontend/src/styles/start-page.css`.

CSS keyframe names are global. The duplication must be removed or route-scoped during cleanup.

### 2.5 Existing reduced-motion support

The major route stylesheets already include `prefers-reduced-motion` handling. This is a good baseline, but CSS media queries do not stop JavaScript tweens.

Every GSAP implementation must therefore use one of the following:

- `gsap.matchMedia()` with `revert()` cleanup;
- a shared React reduced-motion hook plus explicit `kill()`/`revert()` cleanup;
- `@gsap/react` `useGSAP()` scoped to the component container.

## 3. Objectives

### 3.1 Product objectives

- Make report generation feel continuous rather than periodically replaced by polling snapshots.
- Make the report overview feel intentionally composed without turning the report into a marketing page.
- Reduce layout jumps when entering or leaving interview focus mode.
- Preserve the calm, functional, app-like visual language defined by `DESIGN.md`.
- Improve motion consistency across timing, easing, distance, interruption, and reduced-motion behavior.

### 3.2 Engineering objectives

- Split route bundles before adding GSAP.
- Keep GSAP out of routes that do not require it.
- Replace manual tween logic where GSAP provides a safer lifecycle.
- Stop using React `key` primarily as an animation replay mechanism.
- Ensure every tween and timeline can be killed or reverted on update and unmount.
- Animate compositor-friendly properties whenever possible.
- Preserve all existing polling, retry, streaming, navigation, and accessibility behavior.

## 4. Non-goals

This plan does not authorize:

- backend changes;
- report-generation business-logic changes;
- API contract changes;
- a redesign of the six application routes;
- decorative looping animation;
- parallax or scroll-jacking;
- bounce, elastic, glow, 3D tilt, or exaggerated scale effects;
- animating every card, icon, heading, or table row;
- animating interview output token by token;
- replacing working CSS hover, focus, spinner, skeleton, or short state transitions with GSAP;
- installing ScrollTrigger until a concrete scroll-driven requirement is approved;
- using old static HTML prototype pages as implementation sources.

## 5. Motion ownership rules

| Interaction or state | Owner | Rule |
|---|---|---|
| Hover, pressed, focus, color, border | CSS transition | Keep short and local |
| Spinner, skeleton, cursor blink | CSS keyframes | Functional loops remain CSS |
| Multi-element state sequence | GSAP timeline | Use only when order and interruption matter |
| Dynamic progress or score | GSAP tween | Retarget with `overwrite: "auto"` |
| Viewport visibility detection | IntersectionObserver or ScrollTrigger | Detection and animation ownership must remain distinct |
| Component identity | React `key` | Do not change keys solely to replay motion |
| Streaming text | React rendering/native scrolling | Do not tween each chunk |
| Failure or dangerous warning | Immediate state render | Do not delay critical information for an entrance animation |

## 6. Motion tokens

If GSAP is adopted, create one shared motion configuration instead of hard-coding timing in each component.

Proposed values:

```js
export const motionDuration = {
  instant: 0,
  fast: 0.14,
  standard: 0.24,
  deliberate: 0.48,
  progress: 0.6,
};

export const motionEase = {
  enter: "power2.out",
  exit: "power1.in",
  emphasize: "power3.out",
};

export const motionDistance = {
  subtle: 4,
  standard: 8,
  panel: 12,
};
```

Constraints:

- ordinary content displacement: no more than 8px;
- panel displacement: no more than 12px;
- list stagger interval: 40–90ms;
- ordinary state transition: 140–240ms;
- dynamic number/progress transition: 450–700ms;
- report-overview master sequence: no more than approximately 1.1s;
- no elastic or bounce easing;
- no decorative infinite GSAP timelines.

## 7. Implementation plan

### Task 0 — Establish a clean, measurable baseline

**Purpose:** Make bundle, behavior, and motion regressions measurable before code changes.

**Inspect:**

- `frontend/package.json`
- `frontend/src/App.jsx`
- `frontend/src/main.jsx`
- `frontend/src/styles/index.css`
- all route-specific CSS files

**Steps:**

1. Record `git status --short` and preserve unrelated user changes.
2. Run the existing frontend lint/check command.
3. Run the existing frontend unit/browser test suite where available.
4. Build the current frontend.
5. Record generated JS and CSS chunk names and sizes.
6. Exercise all six routes at desktop and narrow viewport sizes.
7. Exercise each route with `prefers-reduced-motion: reduce`.
8. Record current behavior for report progress updates, report overview entry, interview autoscroll, and focus mode.
9. Do not use screenshots unless the user explicitly requests visual capture.

**Commands:**

```powershell
npm --prefix frontend run check
npm --prefix frontend run build
```

Run project-specific browser tests if their current configuration is available and stable.

**Acceptance:**

- baseline commands and output sizes are recorded;
- existing failures are distinguished from changes introduced by this plan;
- no application files have been changed during baseline collection.

### Task 1 — Split route modules and route CSS

**Purpose:** Prevent GSAP and unrelated route styles from entering every route's initial bundle.

**Modify:**

- `frontend/src/App.jsx`
- potentially `frontend/src/main.jsx`
- potentially page-level import/export declarations

**Steps:**

1. Replace static page imports in `App.jsx` with `React.lazy()` dynamic imports.
2. Wrap selected route rendering with a minimal `Suspense` fallback.
3. Keep the fallback app-like and quiet; do not introduce a marketing-style loading screen.
4. Keep shared reset, tokens, typography, and shell styles global.
5. Ensure page-specific styles are imported by their page modules rather than `main.jsx`.
6. Confirm direct navigation to every route still works under the Vite development server and production preview.
7. Confirm the unknown-route fallback still renders correctly.
8. Rebuild and record route chunk sizes.

**Implementation note:**

Named exports can be wrapped during lazy loading:

```jsx
const ReportProcessingPage = lazy(() =>
  import("./pages/ReportProcessingPage").then((module) => ({
    default: module.ReportProcessingPage,
  }))
);
```

Alternatively, migrate page modules to default exports in a separate, mechanical change. Do not mix unrelated component refactors into this task.

**Acceptance:**

- the six route pages are emitted as lazy chunks or otherwise demonstrably split;
- `/prep` does not eagerly load report-detail GSAP code;
- page-specific CSS follows the relevant page chunk where Vite supports it;
- direct route loading and refresh behavior remain unchanged;
- no route displays a blank screen while its chunk loads;
- bundle size evidence is recorded before and after the task.

### Task 2 — Add the smallest GSAP foundation

**Purpose:** Introduce GSAP with React-safe scoping and shared motion policy.

**Modify:**

- `frontend/package.json`
- lockfile used by the project

**Create:**

- `frontend/src/motion/config.js`
- `frontend/src/hooks/useReducedMotion.js`, only if an equivalent shared hook does not already exist

**Recommended dependency command:**

```powershell
npm --prefix frontend install gsap @gsap/react
```

**Steps:**

1. Add `gsap` and `@gsap/react` only.
2. Do not add ScrollTrigger, Flip, or other optional plugins in this task.
3. Register `useGSAP` once according to the package's React integration requirements.
4. Add shared duration, easing, and distance tokens.
5. Consolidate reduced-motion detection into a reusable hook or `gsap.matchMedia()` pattern.
6. Define a documented cleanup convention:
   - component-scoped `gsap.context()` or `useGSAP()`;
   - kill dynamic tweens before replacing them when necessary;
   - revert media-query contexts on unmount;
   - use `overwrite: "auto"` for rapidly retargeted values.
7. Confirm that importing GSAP only from lazy report pages keeps it out of unrelated initial chunks.

**Acceptance:**

- GSAP is not imported globally from `main.jsx`;
- no page animation has been changed yet;
- the application builds without duplicate GSAP bundles;
- reduced-motion and cleanup conventions are documented in code;
- the GSAP chunk is loaded only by routes that use it.

### Task 3 — Refactor `/report-processing` state continuity

**Purpose:** Replace polling-driven animation replay with one interruptible visual state model.

**Modify:**

- `frontend/src/pages/ReportProcessingPage.jsx`
- `frontend/src/styles/report-processing-app.css`
- optional new component-level tests for report-processing motion state

**Do not modify:**

- progress API contracts;
- polling cadence or adaptive polling logic;
- retry/requeue behavior;
- stalled-report detection;
- report error semantics;
- navigation after completion.

#### Task 3.1 — Decouple React identity from animation replay

Remove dynamic keys whose principal purpose is replaying an animation, including candidates on:

- active stage heading;
- active stage description;
- displayed percentage;
- synchronization message;
- action guidance.

Retain keys that represent genuine list identity, such as stable event identifiers. If the API lacks a stable event id, derive the narrowest stable key without using a random or time-varying value.

#### Task 3.2 — Implement retargetable progress motion

1. Keep the semantic `percent` value in React state.
2. Store the currently displayed value in a GSAP proxy/ref.
3. Tween from the currently displayed value to the newest target.
4. Use `overwrite: "auto"` so a new poll retargets the active tween.
5. Update the visible text in `onUpdate` without remounting its DOM node.
6. Animate the progress fill with `scaleX`, not layout-heavy `width`.
7. Preserve `role="progressbar"`, `aria-valuenow`, and `aria-valuetext` using the real semantic value.
8. In reduced-motion mode, set the final value immediately.
9. On completion, failure, interruption, or unmount, kill the active tween.

#### Task 3.3 — Add one semantic stage-transition timeline

1. Track the previous semantic stage in a ref.
2. Run a transition only when the stage changes.
3. Do not replay the transition when only percent, heartbeat, timestamp, or unchanged metadata changes.
4. Sequence a restrained transition:
   - old copy fades and moves upward by at most 4px;
   - the stage anchor updates;
   - new title/copy enters from at most 8px;
   - the newly appended event follows with a short overlap.
5. Keep the total stage transition under approximately 650ms.
6. Immediately render failure, interruption, and dangerous warnings without waiting for a decorative sequence.

#### Task 3.4 — Animate only newly appended events

1. Track which event identities have already appeared.
2. Animate only new rows.
3. Do not stagger or replay the full event ledger on every poll.
4. Limit entry to `autoAlpha` and a horizontal displacement of no more than 8px.
5. Do not animate event-row removal unless removal becomes a real product behavior.

#### Task 3.5 — Remove superseded CSS keyframes

Audit the following current animation families:

- `processing-state-update`;
- `processing-value-update`;
- `processing-current-enter`;
- `processing-stage-anchor`;
- `processing-event-enter`;
- `processing-notice-enter`;
- `processing-status-icon`;
- `processing-action-ready`.

Delete only keyframes and selector rules fully superseded by the GSAP implementation. Retain CSS hover, focus, spinner, and simple status-color transitions.

**Acceptance:**

- a progress update from 20 to 35 to 55 retargets from the current rendered value without jumping back;
- unchanged poll snapshots trigger no entrance animation;
- a stage change produces one coherent transition;
- only new event rows animate;
- failure information is visible immediately;
- polling and retry behavior are unchanged;
- there are no active tweens after route unmount;
- reduced-motion mode applies final states immediately;
- screen-reader progress semantics remain correct.

### Task 4 — Refactor `/report-detail` overview orchestration

**Purpose:** Replace manual numeric tweening and fragmented overview animations with one scoped sequence.

**Modify:**

- `frontend/src/pages/ReportDetailPage.jsx`
- `frontend/src/styles/report-detail-app.css`
- optional focused tests for the animated score and reduced-motion behavior

#### Task 4.1 — Replace manual score `requestAnimationFrame`

1. Remove the manual timing loop in `AnimatedScore`.
2. Tween a numeric proxy from the current displayed score to the target score.
3. Use approximately 680ms with `power3.out`, matching the existing visual intent.
4. Use `overwrite: "auto"` if the score can change while mounted.
5. Kill the tween on update and unmount.
6. Render the final number immediately under reduced motion.

#### Task 4.2 — Build one report-overview timeline

Coordinate only the high-value overview elements:

1. score number;
2. total-score track;
3. dimension bars;
4. top findings or primary insight rows.

Recommended order:

```text
stable title and metadata
  -> score and score track begin together
  -> dimension bars begin around 35% into the score tween
  -> top findings enter with a restrained stagger
```

Constraints:

- maximum total duration approximately 1.1s;
- no bounce, elastic, rotation, or 3D transform;
- bar fills use `scaleX` and a left transform origin;
- row movement is no more than 8px;
- do not animate every report section merely because it becomes visible.

#### Task 4.3 — Preserve the navigation observer

The observer used for active-section navigation is application state logic. Keep it unless a separate navigation refactor proves it unnecessary.

The reveal observer may either:

- remain as visibility detection that triggers a scoped GSAP sequence; or
- be removed for overview elements if the overview timeline owns their one-time entry.

Do not install ScrollTrigger unless a later requirement needs scroll direction, scrub, pinning, or more advanced trigger lifecycle.

#### Task 4.4 — Retain appropriate CSS effects

Keep CSS ownership for:

- skeleton loading;
- button hover/pressed/focus states;
- badge and status-color transitions;
- simple disclosure styling;
- non-coordinated one-off feedback where interruption is not required.

#### Task 4.5 — Remove superseded keyframes

Audit the current 13 report-detail keyframes and remove only those replaced by the overview timeline or score tween. Do not perform a blanket deletion.

**Acceptance:**

- the score ends on the exact API value;
- the score, score track, dimension bars, and findings read as one sequence;
- the active-section navigation remains correct during manual scrolling;
- no report body section repeatedly performs a large entrance animation;
- download, disclosure, skeleton, and focus behavior remains intact;
- reduced-motion mode displays final values and bars immediately;
- all contexts/tweens are reverted or killed on route unmount.

### Task 5 — Evaluate and optionally implement `/interview` focus-mode motion

**Purpose:** Remove the abrupt layout jump caused by immediate side-panel unmounting.

**Modify if approved after Tasks 3–4:**

- `frontend/src/pages/InterviewPage.jsx`
- `frontend/src/styles/interview-app.css`

**Preserve:**

- streaming transport and chunk handling;
- immediate autoscroll during streaming;
- user-controlled scroll-follow behavior;
- Escape-to-exit behavior;
- answer submission and report-generation navigation;
- keyboard focus order.

#### Task 5.1 — Keep streaming motion unchanged

Do not create a tween per token or per streaming chunk. Continue using immediate scrolling while streaming and smooth/controlled scrolling only for committed message changes where appropriate.

#### Task 5.2 — Make side-panel lifetime animation-safe

The current `!focusMode` conditional removes side panels immediately. Before animating:

1. retain side-panel DOM long enough to play the exit sequence;
2. use `aria-hidden` and `inert` when panels become non-interactive;
3. ensure hidden panels cannot receive keyboard focus;
4. avoid preserving hidden panels in a way that affects layout after the transition.

#### Task 5.3 — Implement a reversible focus-mode sequence

Entering focus mode:

1. fade/translate left and right panes by at most 12px;
2. disable their interaction;
3. expand the central conversation pane;
4. preserve focus on the focus-mode control or move it intentionally to the main region.

Exiting focus mode:

1. restore the three-column layout;
2. return the central pane to its normal width;
3. fade/translate side panes back into place;
4. restore interaction and correct accessibility state.

Use a timeline that can be reversed or safely overwritten if the button is pressed rapidly.

**Acceptance:**

- focus mode no longer produces an abrupt visual jump;
- rapidly toggling focus mode ends in the correct state;
- Escape exits correctly at any point in the transition;
- streaming text does not stutter;
- user manual scrolling is not overridden;
- hidden panels are not exposed to keyboard or accessibility navigation;
- reduced-motion mode changes layout immediately.

### Task 6 — Perform CSS motion cleanup without expanding GSAP

**Purpose:** Reduce unnecessary motion and eliminate global duplication on routes that do not need GSAP.

#### Task 6.1 — `/help`

**Modify:**

- `frontend/src/pages/HelpPage.jsx`
- `frontend/src/styles/help-app.css`

Actions:

1. Stop using changing keys solely to replay chapter animations.
2. Retain one short content crossfade or a maximum 4px content shift.
3. Retain the active navigation indicator.
4. Remove repeated row stagger when switching among the three help views.
5. Remove repeated icon-settle animation.
6. Keep errors and recovery instructions immediately readable.

GSAP must not be introduced on this route unless a future interaction becomes materially more complex.

#### Task 6.2 — `/prep`

Keep CSS for:

- initial pane entrance;
- spinner/loading feedback;
- buttons and form states;
- validation and error display;
- focus restoration.

An optional GSAP plan-generated sequence may be considered only after higher-priority work is complete and only if the current CSS reveal is demonstrably fragmented.

#### Task 6.3 — `/reports`

Keep the route CSS-only. Preserve:

- sync progress animation;
- processing spinner;
- restrained ledger-row feedback;
- search/date composite focus behavior.

GSAP Flip may be reconsidered only if live reordering, draggable sorting, or position-preserving filtering becomes a real product requirement.

#### Task 6.4 — Global cleanup

1. Remove or rename the duplicate `start-spin` keyframe.
2. Audit globally imported legacy animation selectors in `styles/index.css`.
3. Remove unused keyframes only after verifying selector usage with code search and tests.
4. Do not alter the established focus-ring solution as part of animation cleanup.

**Acceptance:**

- `/help` feels calmer during repeated navigation;
- `/prep` and `/reports` do not load GSAP;
- no duplicate global keyframe names remain;
- no functional spinner or loading state is removed;
- keyboard focus indicators remain visible and visually consistent.

### Task 7 — Accessibility, performance, and regression verification

**Purpose:** Verify that motion polish has not weakened usability, reliability, or loading performance.

#### Accessibility matrix

Verify every changed route with:

- keyboard-only navigation;
- Escape where applicable;
- `prefers-reduced-motion: reduce`;
- rapid repeated interaction;
- screen-reader-relevant labels and progress semantics;
- browser zoom at 200%;
- narrow viewport/reflow behavior.

Reduced-motion requirements:

- scores immediately show their final values;
- progress immediately reaches the latest semantic value;
- stage changes do not require spatial motion;
- focus-mode layout changes immediately;
- content is never initially hidden while waiting for a skipped animation;
- functionality and information order are unchanged.

#### Performance matrix

Verify that animations primarily modify:

- `x`/`y` transforms;
- `scale`/`scaleX`;
- `autoAlpha`/`opacity`.

Avoid continuous animation of:

- `width` and `height`;
- `top` and `left`;
- large-area blur/filter;
- large shadow interpolation;
- background-position gradients;
- full-page backdrop filters.

Measure:

- initial JS/CSS sizes per route;
- presence and size of the GSAP chunk;
- whether non-GSAP routes fetch that chunk;
- layout shift during report stage changes;
- long tasks while report progress is updating;
- animation behavior under CPU throttling;
- orphaned animation callbacks after navigation.

#### Functional regression matrix

`/report-processing`:

- loading;
- adaptive polling;
- stage update;
- percent update;
- new event append;
- stalled state;
- retry/requeue;
- failed state;
- completed navigation;
- route unmount while polling.

`/report-detail`:

- skeleton/loading;
- score render;
- overview sequence;
- section navigation;
- disclosure controls;
- download feedback;
- route unmount during animation.

`/interview`:

- stream start/chunks/completion;
- committed-message scroll;
- manual upward scrolling;
- focus-mode entry/exit;
- rapid toggle;
- Escape;
- submit answer;
- generate report.

All routes:

- direct navigation;
- refresh;
- unknown route;
- desktop and narrow layouts;
- reduced motion;
- keyboard focus.

**Commands:**

```powershell
npm --prefix frontend run check
npm --prefix frontend run build
```

Run the project's existing frontend/browser tests after locating their current command and configuration. Do not create a new test runner merely for this plan if the repository already has one.

**Acceptance:**

- lint/check passes;
- production build passes;
- existing browser tests pass;
- new focused motion lifecycle tests pass where added;
- route-level chunks remain split;
- GSAP is not loaded by routes that do not use it;
- no accessibility or functional regressions are found.

## 8. Proposed file inventory

### Expected modifications

- `frontend/package.json`
- frontend lockfile
- `frontend/src/App.jsx`
- potentially `frontend/src/main.jsx`
- `frontend/src/pages/ReportProcessingPage.jsx`
- `frontend/src/pages/ReportDetailPage.jsx`
- optionally `frontend/src/pages/InterviewPage.jsx`
- `frontend/src/pages/HelpPage.jsx`
- `frontend/src/styles/report-processing-app.css`
- `frontend/src/styles/report-detail-app.css`
- optionally `frontend/src/styles/interview-app.css`
- `frontend/src/styles/help-app.css`
- `frontend/src/styles/start-app.css` or `frontend/src/styles/start-page.css` for duplicate keyframe cleanup
- potentially `frontend/src/styles/index.css` after selector-usage verification

### Expected creations

- `frontend/src/motion/config.js`
- optionally `frontend/src/hooks/useReducedMotion.js`
- focused frontend tests if no existing test location covers the changed lifecycle

### Explicitly out of scope

- `app/`
- backend service and route code
- database migrations
- report-worker code
- RAG/embedding configuration
- static six-page HTML prototypes

## 9. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| GSAP enters the main bundle | Every route pays the dependency cost | Complete lazy route splitting first; import GSAP only in target pages |
| New polls fight active progress tween | Jitter or incorrect rendered value | Use one proxy and `overwrite: "auto"` |
| Stale timeline updates a remounted page | Visual corruption or React warnings | Scope with `useGSAP()`/context and revert on unmount |
| Reduced motion only exists in CSS | JS animation still runs | Use `gsap.matchMedia()` or shared hook in every GSAP component |
| Dynamic keys continue remounting nodes | GSAP cleanup and semantics become unstable | Remove animation-only keys before attaching timelines |
| Error content waits for a timeline | User cannot diagnose failure promptly | Render critical failures immediately and kill decorative motion |
| Focus-mode side panels unmount too early | Exit animation cannot run | Separate visual transition state from final mounted/interactive state |
| Hidden side panels remain focusable | Accessibility regression | Coordinate `inert`, `aria-hidden`, focus, and DOM lifetime |
| Layout-heavy properties are animated | Reflow and jank | Prefer transforms and opacity; use `scaleX` for bars |
| Excessive report-section reveals | App feels theatrical and slow | Restrict orchestration to overview and meaningful disclosures |
| Existing CSS keyframes remain active | Double animation | Remove only verified superseded rules after GSAP behavior is tested |
| Concurrent unrelated work is overwritten | User changes are lost | Inspect status/diff before every batch and patch only scoped files |

## 10. Rollback strategy

Implementation should be divided into reversible commits. Do not combine route splitting, GSAP foundation, all page migrations, and CSS cleanup in one commit.

Recommended commit sequence:

1. `perf(frontend): split application route bundles`
2. `chore(frontend): add scoped gsap motion foundation`
3. `feat(frontend): orchestrate report processing progress motion`
4. `feat(frontend): coordinate report detail overview motion`
5. `feat(frontend): smooth interview focus mode transition` — optional
6. `refactor(frontend): simplify css motion and remove duplicate keyframes`
7. `test(frontend): verify motion lifecycle and reduced motion`

Rollback rules:

- If route splitting fails direct-navigation behavior, revert Task 1 before adding GSAP.
- If report-processing animation affects polling or completion behavior, revert Task 3 independently.
- If report-detail navigation tracking regresses, restore the existing navigation observer and roll back only the reveal orchestration.
- If focus-mode accessibility cannot be preserved, retain the current immediate layout change and defer Task 5.
- Do not roll back unrelated user changes in a dirty worktree.

## 11. Definition of done

The plan is complete only when all applicable statements are true:

- route-level code splitting is implemented and measured;
- GSAP is loaded only on routes that use it;
- report progress retargets smoothly without remounting the percentage node;
- only semantic stage changes run a stage timeline;
- only newly appended report events animate;
- report failure and interruption states display immediately;
- the report-detail score no longer uses a manual RAF tween;
- report overview elements form one restrained sequence;
- section navigation remains accurate;
- interview streaming remains free of per-chunk GSAP animation;
- optional focus-mode motion preserves keyboard and accessibility behavior;
- `/prep`, `/reports`, and `/help` remain free from unnecessary GSAP usage;
- duplicate global keyframe names are removed;
- every tween and timeline has an explicit cleanup path;
- reduced-motion mode uses immediate final states;
- frontend check, build, and browser regression tests pass;
- bundle-size and behavior evidence are recorded before and after implementation;
- the resulting motion remains consistent with the calm, app-like direction in `DESIGN.md`.

## 12. Execution recommendation

Execute Tasks 0–4 first and pause for product review.

That first delivery should contain:

1. route-level code splitting;
2. the scoped GSAP foundation;
3. report-processing state continuity;
4. report-detail overview orchestration;
5. verification evidence.

Only after those changes are stable should the team decide whether interview focus mode needs GSAP. The help, preparation, and report-list pages should be handled as CSS cleanup work rather than GSAP migration.

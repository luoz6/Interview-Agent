# Stage 30 Browser Versioned Resume Acceptance

Stage 30 wires the browser interview page into the versioned HTTP resume contract.

## Scope

- Page: `/interview?session_id=...`
- Resume handshake: `GET /api/interviews/{session_id}`
- Mutating commands: answer stream, skip, finish
- Required command fields: `expected_version`, `command_id`
- Required recovery behavior: on `409`, reload `GET /api/interviews/{session_id}` and keep the user's answer available for retry. Skip and finish conflicts are not auto-retried; the user retries after the refreshed state is visible.

## Manual Verification Checklist

- [ ] Start from `/prep` and create a new interview.
- [ ] Confirm the first interview snapshot includes `state_version`.
- [ ] Submit a streamed answer.
- [ ] Confirm the streamed answer request payload contains `expected_version` and `command_id`.
- [ ] Refresh the interview page.
- [ ] Confirm the conversation, current question, question states, and tags are restored.
- [ ] Submit or skip after refresh.
- [ ] Confirm the next request uses the refreshed `state_version`.
- [ ] Trigger a stale command or simulate a `409` response.
- [ ] Confirm the page reloads the latest snapshot.
- [ ] Confirm the user's unsent or failed answer remains in the textarea.
- [ ] Confirm stale skip or finish does not auto-retry and succeeds after one manual retry.
- [ ] Finish the interview and continue to report processing.
- [ ] Confirm report detail still renders and PDF download remains available.

## Result

- Date: 2026-07-08
- Environment: Local V1 Windows runtime
- Browser:
- Session ID:
- Result:
- Notes:

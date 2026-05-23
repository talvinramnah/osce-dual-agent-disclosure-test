# Chat History (Last 10 Sessions)

## Background and Motivation

User wants to be able to look back at past full conversations in the OSCE app. Decisions confirmed up front:

- **Scope:** a "conversation" = a full past session with a patient (not last 10 message turns within the current chat).
- **Persistence:** in-memory only. Stored in `st.session_state` so it lives for the duration of the browser session and is lost on refresh / instance restart / redeploy. No disk, no DB.
- **Grouping:** global last 10 across all patients (FIFO eviction — oldest dropped when an 11th is added).
- **Download:** user can download/save any chat (live or archived) to their own machine as a file. Format: Markdown (human-readable, good for OSCE review/sharing). JSON optional follow-up.
- **Mode:** Planner now, Executor after user approval.

**Resolved decisions** (2026-05-23, second round):
- Reset = archive (not discard). One button, both behaviours.
- Disclosure-debug pane will show "live only" note when viewing an archived session (per-turn meta not archived to keep memory small).

The current app only tracks one in-flight conversation in `st.session_state.history` and wipes it on "Reset conversation" or patient switch (`app.py` lines 100-103, 140-148). Nothing is archived. This plan adds an archive + a way to view past sessions.

## Key Challenges and Analysis

1. **When to archive a session.** Two natural boundaries already exist in `reset_conversation()` and the patient-switch branch. Both currently throw the live history away. We hook into both to snapshot first, then reset. We also archive on a new explicit "End & save" action so the user can checkpoint a finished case without switching patients.
   - Empty sessions (zero turns) must NOT be archived — avoids junk entries from incidental resets / patient toggling.

2. **Per-user, not per-process.** Use `st.session_state` for the archive too, not `@st.cache_resource`. Cache-resource is process-wide and would leak past sessions between different visitors on Streamlit Cloud. `st.session_state` is per browser session, which matches "in-memory only" and avoids a privacy issue.

3. **Don't store audio bytes in the archive.** Each TTS clip is tens-to-hundreds of KB. Ten sessions × ~10 turns × audio = quick memory bloat for a feature that's just for re-reading. Strip `audio` when archiving; keep text only. The live conversation still has audio; archived view is read-only text.

4. **Viewing vs. live mode.** Need a clear visual switch:
   - Default: live conversation as today.
   - When user clicks a past session in the sidebar, the left "Conversation" pane re-renders that archived history (read-only, no input box, no disclosure debug — or a faded version) with a prominent "← Back to live" button.
   - Switching patient or hitting Reset while *viewing* a past session should just return to live for the currently selected patient, not modify the live state.

5. **Identity & ordering.** Each archived entry needs:
   - `id` — monotonically increasing int (simple counter in session state). UUID is overkill.
   - `patient_id` + `patient_display_name` (denormalised so we don't depend on registry lookups later).
   - `started_at` / `ended_at` ISO timestamps (UTC).
   - `turns` — list of `{student, patient}` (no audio).
   - `earned` — list of fact IDs disclosed in that session.

   Sidebar list ordered newest-first.

6. **FIFO cap = 10.** When appending an 11th, pop index 0. Done.

7. **What about the "Disclosure debug" right-hand pane when viewing a past session?** It's tied to `last_meta` which is per-turn, and we don't archive per-turn meta (would bloat memory and isn't asked for). Simplest answer: when viewing an archived session, hide / disable the debug pane and show a note like "_Disclosure debug is only available for live conversations._". Confirm with user, but this is my recommendation.

8. **No new dependencies.** Pure Streamlit + stdlib (`datetime`, maybe `uuid` — but we won't need it).

9. **Download format.** Markdown is the right default — readable in any text editor / GitHub / preview pane, useful for OSCE marking, easy to paste into a logbook. A small `format_session_as_markdown(session)` helper takes either a live `(patient_id, history, earned)` triple or an archived dict and produces the same output. Streamlit's `st.download_button(label, data, file_name, mime)` handles the actual download — no JS, no extra deps. Filename: `osce-{patient_id}-{YYYYMMDD-HHMMSS}.md` using `ended_at` (or "now" for live).

   Markdown layout:
   ```
   # OSCE Conversation — {Patient Display Name}

   - **Started:** 2026-05-23 18:32 UTC
   - **Ended:** 2026-05-23 18:45 UTC   (omitted for live)
   - **Turns:** 5
   - **Facts disclosed:** 8

   ---

   **Student:** What brings you in today?

   **Patient:** Oh, doctor, this pain in my side is killing me…

   ---

   **Student:** …

   ---

   ## Facts disclosed
   - `pain_site`
   - `pain_onset_timing`
   - …
   ```

## High-level Task Breakdown

Each task has a verifiable success criterion. Executor does them one at a time and checks in.

### Task 1 — Session state additions + archive helper

- Add to `init_state()`:
  - `st.session_state.sessions` = `[]`  (archive list)
  - `st.session_state.session_counter` = `0`
  - `st.session_state.session_started_at` = `None`  (set when first turn lands)
  - `st.session_state.viewing_session_id` = `None`  (None = live mode)
- Add a pure helper `archive_current_session()` that:
  - Returns early if `history` is empty.
  - Builds the archive dict (id, patient_id, display name from registry, started_at, ended_at=now, turns stripped of `audio`, earned).
  - Appends to `sessions`, evicts index 0 if `len > 10`.
  - Bumps `session_counter`.
- Update `reset_conversation()` to call `archive_current_session()` *before* clearing live state. Also reset `session_started_at` to `None`.

**Success criteria:** unit-style smoke check — temporarily print/log `len(st.session_state.sessions)` after a manual reset with non-empty history and confirm it goes 0 → 1; reset again with empty history and confirm it stays at 1; archive entries have no `audio` keys in `turns`.

### Task 2 — Hook archive into patient switch

- In the existing `if selected_id != st.session_state.patient_id:` block (currently lines 140-143), call `archive_current_session()` before `reset_conversation()`.
- `session_started_at` should be set on the first turn of any new session (in the submission handler, only if currently `None`).

**Success criteria:** Have a non-empty chat with Michael, switch to Daniel, verify a Michael entry appears in the archive list (built in Task 4); chat with Daniel, switch back, verify a Daniel entry now exists too. Switching when current chat is empty does NOT create an entry.

### Task 3 — "End & save" button

- Add a button in the sidebar (next to / under the existing "Reset conversation") labelled "End & save session". Disabled when `history` is empty.
- On click: `archive_current_session()` then `reset_conversation()` then rerun.
- Existing "Reset conversation" stays as-is in behaviour: it now archives (because we changed `reset_conversation()` in Task 1). User confirmed Reset = archive (no separate Discard button needed).

**Success criteria:** Button appears, is disabled with empty chat, archives + clears with non-empty chat.

### Task 3b — Markdown formatter helper

- Add `format_session_as_markdown(*, patient_display_name, started_at, ended_at, turns, earned) -> str` to `app.py` (or a new small `session_export.py` if it grows).
- Works for both live and archived data — caller passes `ended_at=None` for live and the function omits that line.
- Handles edge case of empty `turns` (returns header only) and empty `earned` (omits the section or shows "_(none)_").

**Success criteria:** Calling it on a hand-built sample produces the exact layout in the spec above; no crashes on empty inputs; output is valid Markdown that renders cleanly in a preview.

### Task 4 — Sidebar "Past sessions" UI

- New sidebar section under a `st.markdown("---")` and `st.subheader("Past sessions")` (or expander, TBD on usability).
- If `sessions` is empty: show "_No past sessions yet._".
- Otherwise: render newest-first as a list of buttons. Each button label:
  - `"{display_name} · {N} turns · {HH:MM}"` using local time of `ended_at`.
- Clicking a button sets `viewing_session_id` to that entry's id and reruns.
- A "← Back to live" button shown only when `viewing_session_id is not None`.

**Success criteria:** After archiving two sessions, both are listed newest-first with correct labels; clicking one toggles viewing mode; back button returns to live.

### Task 4b — Download button (live + archived)

- **Live chat:** in the sidebar, under the "End & save" button, add `st.download_button` labelled "Download chat (.md)". Disabled when `history` is empty. Builds Markdown on demand using current live state, filename `osce-{patient_id}-{now}.md`.
- **Archived chat (read-only view, from Task 5):** in the banner area at the top of the read-only main pane, place a `st.download_button` for that specific archived session. Filename uses the archived `ended_at`.
- Both share `format_session_as_markdown()` from Task 3b. No duplication.

**Success criteria:** Clicking Download on a live non-empty chat downloads a `.md` file with the right filename and contents; same for an archived chat; both render correctly when opened in a Markdown previewer; button is disabled / hidden when there's nothing to download.

### Task 5 — Read-only past-session view in main pane

- Refactor the left "Conversation" pane:
  - If `viewing_session_id` is set, look it up and render its `turns` (no audio, no input form, banner at top: `"Viewing past session: {display_name} · started {…} · ended {…}"`).
  - Otherwise: render live conversation + input as today.
- Right pane "Disclosure debug":
  - If viewing a past session, replace contents with note: `"Disclosure debug is only available for live conversations."`
  - Otherwise unchanged.
- Patient sidebar radio + Reset button remain interactive, but:
  - Switching patient while viewing past should return to live for the new patient (clear `viewing_session_id`, then go through existing switch flow which archives the *live* chat — viewing past doesn't touch live).
  - Reset while viewing past should also flip back to live before resetting.

**Success criteria:** Clicking a past session shows its full transcript read-only, no input box, no audio playback widgets, debug pane shows the note. Clicking back returns to where the user was.

### Task 6 — Manual smoke test (user)

User runs locally:
1. Have a 3-turn convo with Michael, click Reset → entry 1 appears.
2. Have a 2-turn convo with Daniel via patient switch → entry 2 appears.
3. Click Michael entry → see transcript, no input. Click back → return to live Daniel.
4. Have a 1-turn convo, click "End & save" → entry 3 appears.
5. Force 10+ entries to confirm FIFO eviction at 11.
6. Click Download on a live chat → `.md` file downloads, opens cleanly. Repeat from an archived chat.
7. Refresh page → archive is empty (confirms in-memory only, matches spec).

### Task 7 — README / inline note

- Short note in `README.md` under a "Features" or new "Session history" subheading: history is in-memory, last 10, cleared on refresh, downloadable as Markdown.

## Project Status Board

- [x] Task 1: state additions + archive_current_session helper, hook into reset
- [x] Task 2: archive on patient switch + track session_started_at
- [x] Task 3: "End & save session" sidebar button
- [x] Task 3b: Markdown formatter helper
- [x] Task 4b (live half): Download .md button in sidebar for current chat
- [x] Task 4: sidebar "Past sessions" list
- [x] Task 5: read-only past-session view in main pane
- [x] Task 4b (archived half): Download .md button in read-only view
- [ ] Task 6: user smoke test
- [ ] Task 7: README note

## Current Status / Progress Tracking

**Milestone A complete** (Executor, 2026-05-23). Tasks 1, 2, 3, 3b, and the live half of 4b implemented in `app.py`. Formatter smoke-tested on three input shapes (full / empty turns / empty earned) and on `_filename_stamp` with real, None, and garbage inputs — all pass. No new dependencies. No linter errors.

**Milestone B complete** (Executor, 2026-05-23). Tasks 4, 5, and the archived half of 4b implemented in `app.py`. New helpers `_format_local_time`, `_session_label`, `_find_session` added; smoke-tested for valid / None / bad / full-format inputs. Sidebar now shows "Past sessions (N/10)" with newest-first buttons; clicking renders a read-only view in the main pane (3-column banner with patient meta, Back-to-live button, Download .md button). Currently-viewed session button is disabled to indicate selection. FIFO eviction handled; viewing-id self-heals if the entry gets evicted. End&save / Reset now also clear `viewing_session_id` so they bounce back to live as a side-effect.

Awaiting user manual verification before Tasks 6 (full smoke test) and 7 (README note).

## Executor's Feedback or Assistance Requests

**Milestone B is ready for you to manually verify.** Full feature is now visible end-to-end:

- ✅ Sidebar has new "Past sessions (N/10)" section under the live action buttons.
- ✅ Each past entry is a button labelled `"{patient} · {N turns} · {HH:MM}"` in local time, newest first. The currently-viewed entry is disabled so you can see which one is open.
- ✅ Clicking a past entry replaces the main pane with a read-only banner (patient name, start/end times, turn + fact counts), a `← Back to live` button, and a `Download (.md)` button.
- ✅ The read-only transcript renders below the banner — no input form, no audio playback.
- ✅ Right pane shows "Disclosure debug is only available for live conversations" plus a list of facts disclosed in that archived session.
- ✅ Hitting Reset / End & save / patient-switch while viewing past bounces you back to live automatically.
- ✅ If a viewing id has been evicted by FIFO before render (edge case), it self-heals to None.

Suggested manual smoke test (this is Task 6 in the board):
1. Have a 3-turn convo with Michael, click End & save → entry 1 appears in the sidebar list.
2. Switch to Daniel, have a 2-turn convo, click Reset → entry 2 appears.
3. Click Michael entry → main pane shows transcript, no input form, banner with name + times + counts.
4. Click Download (.md) in the banner → file downloads, opens cleanly.
5. Click ← Back to live → returns to live Daniel view (empty after reset).
6. Force 10+ archived sessions to confirm FIFO drops the oldest at 11.
7. Refresh the page → archive empties (confirms in-memory only, matches spec).

Once that all passes, give me the nod and I'll do Task 7 (small README note) to wrap up.

## Lessons

(none yet for this feature)

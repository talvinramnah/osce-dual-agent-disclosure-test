# Multi-Patient Sidebar + Directory Reorg

## Background and Motivation

User added a second OSCE case (Daniel O'Connor, 20yo M, central chest pain ?pericarditis) under `2nd patient/`. Goals:

1. Streamlit sidebar to switch between patients.
2. Restructure directory so the codebase scales cleanly to N patients.

The core engine (`intent_agent.py`, `patient_agent.py`, `orchestrator.py`, `stt.py`, `tts.py`) is already case-agnostic - it takes fact store + prompts as arguments. Work is mostly file-layout + a small UI change in `app.py`.

## Key Challenges and Analysis

1. **Per-patient intent prompt, not just facts/persona.** Current `prompts/intent_agent_system.md` names Michael Doyle on line 7 and bakes Michael-specific fact IDs into broad-open scope-set rules (`assoc_vomiting`, `assoc_nausea`, `ice_concerns_main`, `ice_concerns_why_surgery`). Daniel's facts use overlapping but different IDs (`assoc_fever`, `ice_concerns`, three ICE facts). So intent prompt is per-patient too. User only supplied Daniel persona + few-shots; we author Daniel's intent prompt by adapting Michael's (case description line + scope-set fact IDs only). Approved.

2. **Caching in `app.py`.** `build_turn()`, `load_fact_store()`, `load_prompts()` are `@st.cache_resource`. Make them parameterized by `patient_id` so switching rebuilds correctly.

3. **Reset on switch.** Switching patient must clear `history`, `earned`, `last_meta`.

4. **Folder name with a space.** `2nd patient/` has a space. Reorg eliminates it.

5. **Duplicate orchestrator.** `2nd patient/orchestrator.py` is stale dup of root - removed.

6. **Layout after reorg.**
   - **Shared (top-level):** `app.py`, `orchestrator.py`, `intent_agent.py`, `patient_agent.py`, `stt.py`, `tts.py`, `requirements.txt`, `README.md`.
   - **Per-patient:** `patients/<id>/{facts.json, intent_system.md, patient_system.md, disclosure_few_shots.yaml}`.
   - **Patient registry:** `patients/registry.py` lists available cases.

## Proposed New Directory Layout

```
bleep64-osce-prototype/
├── app.py
├── orchestrator.py
├── intent_agent.py
├── patient_agent.py
├── stt.py / tts.py / requirements.txt / README.md
├── patients/
│   ├── __init__.py
│   ├── registry.py
│   ├── michael_doyle/
│   │   ├── facts.json
│   │   ├── intent_system.md
│   │   ├── patient_system.md
│   │   └── disclosure_few_shots.yaml
│   └── daniel_oconnor/
│       ├── facts.json
│       ├── intent_system.md          (NEW: adapted from Michael's)
│       ├── patient_system.md
│       └── disclosure_few_shots.yaml
└── .cursor/
```

Old `facts/`, `prompts/`, `2nd patient/` removed at end.

## Daniel intent prompt - what changes from Michael's

- **Line 7 case description:** "Michael Doyle, A&E, right-sided flank pain" → "Daniel O'Connor, A&E, central chest pain".
- **Broad-open scope sets:**
  - PC duration brief: same.
  - Pain-history surface trio: same fact IDs (`pain_site`, `pain_onset_timing`, `pain_onset_mode`).
  - Associated-symptoms broad: change from "`assoc_vomiting` and `assoc_nausea`" → just "`assoc_fever`" (only assoc symptom that unlocks on broad in Daniel's facts).
  - ICE: change from four facts to Daniel's three (`ice_ideas`, `ice_concerns`, `ice_expectations`); same general rule (one ICE fact may unlock late-turn on broad, rest need direct).

Everything else (utterance taxonomy, filler rules, social-chat handling, JSON output shape) stays.

## High-level Task Breakdown

1. **Create `patients/` skeleton + move Michael's files** - all four via `git mv` (tracked). Remove old empty `facts/` and `prompts/`.
2. **Move Daniel's files into `patients/daniel_oconnor/`** - plain `mv` (untracked). Remove `2nd patient/` ONLY after verifying destinations exist with correct sizes.
3. **Author `patients/daniel_oconnor/intent_system.md`** - adapted from Michael's.
4. **Add `patients/registry.py`** with `PATIENTS` dict + `get_patient(id)` helper.
5. **Refactor `app.py`** - parameterize cached loaders by `patient_id`, drive titles/labels off the registry, default to first patient.
6. **Add sidebar patient selector** - `st.sidebar.radio` listing display names; on change, clear `history` / `earned` / `last_meta` and rerun.
7. **Smoke-test both cases end-to-end** (manual, by user).
8. **Update README** with new layout + how to add a third patient.

## Project Status Board

- [ ] Task 1: patients/ skeleton + Michael moves
- [ ] Task 2: Daniel moves
- [ ] Task 3: Daniel intent_system.md
- [ ] Task 4: patients/registry.py
- [ ] Task 5: app.py refactor
- [ ] Task 6: sidebar selector
- [ ] Task 7: smoke test (user)
- [ ] Task 8: README update

## Current Status / Progress Tracking

Retrying after revert. Plan unchanged. Beginning Task 1.

## Executor's Feedback or Assistance Requests

(none yet)

## Lessons

- **2026-05-23:** Never chain `git mv` (or any failure-sensitive op) with `&&` followed by a destructive `rm -rf` sequenced with `;`. The `;` breaks the short-circuit and lets the destructive command run unconditionally even when moves failed. **For untracked files, use plain `mv` not `git mv`.** Never put `rm -rf` of source dir in the same pipeline as the moves; do it as a separate, post-verification step.
- **2026-05-23:** `rm -rf` on macOS does not route through Trash. No easy undo. Treat `rm -rf` of user-supplied content as terminal.

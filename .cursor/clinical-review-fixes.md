# OSCE Prototype — Clinical Review Fixes

## Background and Motivation

A clinical associate tested the current Michael Doyle prototype end-to-end and surfaced four
classes of issue that compromise clinical realism. The architecture (dual-agent, fact-gated
disclosure) is sound; the problems are concentrated in the gate's classification rules, the
patient agent's prompt and inputs, and a handful of fact-store entries. The goal of this
plan is to address all four findings without introducing new architectural surface area.

The four findings are:

1. **Open-question budget is too small.** Only the first broad open after the presenting
   complaint releases new facts. Subsequent broad opens (which a real student would
   reasonably ask 2–3 of) release nothing.
2. **Inconsistent classification of social/check-in utterances.** "How are you doing today?"
   gets force-fit into `filler_only` and produces a flat "hmm". Later social chat
   ("How are you holding up?") was handled well, exposing that the taxonomy lacks a
   dedicated rapport bucket.
3. **Recap dumping.** When `newly_earned` is empty but `already_earned` is large, the
   patient agent restates a long summary of prior disclosures instead of giving a brief
   deflection. Most visible mid-consultation in ICE/PMH sections.
4. **Inconsistent adherence to canonical wording.** Sometimes the canonical line is
   adapted to fit the question (good); sometimes it is emitted verbatim with an
   ill-fitting "No, …" opener that doesn't match the question's framing.

## Key Challenges and Analysis

### Issue 1 — Open-question budget

The intent_agent_system prompt currently includes:

> Repeat broad prompts in the same domain stop releasing new facts.

This rule is too aggressive **and** is enforced at the wrong granularity (per-domain). A
real consultation has a budget of ~2–3 broad opens that walk through *different* scopes:
pain history surface → associated symptoms surface → maybe ICE surface. The fact store
already contains the raw material for this; the gate is just refusing to map broad opens
across distinct scopes.

A second contributor: only `assoc_vomiting` is configured to unlock on a broad
associated-symptoms ask. Clinically, a patient volunteering associated symptoms on an
open prompt should usually surface 2–3 things (e.g. vomiting, nausea, urinary discomfort).

### Issue 2 — Filler vs social chat

The taxonomy has 7 types: `filler_only`, `broad_open`, `aspect_specific`,
`specific_direct`, `yes_no`, `unclear`, `closing`. There is no bucket for **social /
rapport** utterances ("how are you doing", "how are you holding up", "are you comfortable
to talk?"). These get bucketed as `filler_only`, and the patient prompt instructs filler
turns to reply with a brief sound only — which is wrong for genuine check-ins.

The fix is one new utterance type (`social_chat`) and a corresponding rule in the patient
prompt: brief, in-character emotional response anchored to **persona-level state**
(pain, worry) — never new clinical facts.

### Issue 3 — Recap dumping

Root cause is structural. The orchestrator currently passes the union of newly-earned
and already-earned facts to the patient agent in
`<facts_available_to_disclose_this_turn>`:

```python
all_available_ids = list({*newly_earned_ids, *already_earned})
available_facts = [self.fact_lookup[fid] for fid in all_available_ids ...]
```

When `newly_earned` is empty and `already_earned` is large, the patient sees a wall of
facts in the disclosure block, with no signal that they are "for reference only". The
patient prompt's "do not restate" rule is fighting against the structural cue.

The clean fix is to split the input into two distinct blocks:
- `<newly_authorised_this_turn>` — what the gate just released (these *may* be spoken).
- `<already_disclosed_for_reference_only>` — id-only list of prior disclosures (do NOT
  restate; only briefly reference if directly asked).

The patient prompt then has to enforce: **never recap previously-disclosed content
unless the student explicitly asks for a summary.**

### Issue 4 — Canonical adherence

The patient prompt says "use canonical as a strong anchor … may adjust phrasing very
slightly". With `temperature=0.7` the model swings between verbatim and adapted. The
worst-feeling cases are canonicals that begin with `No, …` or `Yes, …` and get emitted
verbatim against a question that was not yes/no.

Two complementary fixes:
1. Strengthen the patient prompt to require **grammatical fit to the question's framing**
   (drop polarity openers when the question isn't yes/no).
2. Audit and lightly rewrite the small set of canonicals whose shape is too brittle.

## High-level Task Breakdown

Tasks listed in the proposed execution order (smallest, lowest-risk first; fact-store
expansion last). Each task has its own success criteria. Per the workflow rules, the
Executor should pause between tasks for human verification.

---

### Task 1 — Fix recap dumping (Issue 3)

**Scope:**
- `orchestrator.py`: stop merging newly-earned and already-earned into a single
  `available_facts` list. Instead pass both lists separately to `patient_agent.respond`.
- `patient_agent.py`: split the user message into two blocks
  (`<newly_authorised_this_turn>` with id + canonical_response,
  `<already_disclosed_for_reference_only>` with ids only).
- `prompts/patient_agent_system.md`: add an explicit "do not restate previously-disclosed
  facts" rule, and update the "Input format you'll receive" section to describe the new
  blocks.

**Success criteria:**
- A test conversation reaches a state with ≥6 facts already earned. Asking
  "Do you have any idea what happened?" produces a brief deflection (1–2 sentences),
  with NO summary of prior disclosures.
- Asking a follow-up like "Sorry, can you remind me where the pain is?" still produces
  a brief reference to the previously-disclosed site (not a full retell of every fact).
- No regressions on a fresh conversation: opening turns still produce coherent answers
  using the canonical wording.

---

### Task 2 — Add `social_chat` utterance type (Issue 2)

**Scope:**
- `prompts/intent_agent_system.md`: add `social_chat` to the taxonomy with a clear
  definition and disambiguation from `filler_only` and `closing`. Specify that
  `social_chat` returns `newly_earned: []` (no clinical fact unlock).
- `prompts/disclosure_few_shots.yaml`: add 2–3 examples covering
  "How are you doing today?", "How are you holding up?", "I'm sorry to hear that."
- `prompts/patient_agent_system.md`: add a new section for the no-fact branch when
  `utterance_type=social_chat` — brief in-character response anchored to persona-level
  pain/worry, no new clinical content.

**Success criteria:**
- "How are you doing today?" at turn 1 → gate classifies as `social_chat`,
  `newly_earned=[]`; patient responds with something like "Honestly not great, the
  pain is really bad" without disclosing any structured fact (no site, no timing).
- "How are you holding up?" mid-consultation → similarly handled, in-character.
- Pure filler ("Okay", "Right, mm-hm") still classifies as `filler_only` and produces
  a brief acknowledgement only.

---

### Task 3 — Tighten canonical-adherence prompt (Issue 4)

**Scope:**
- `prompts/patient_agent_system.md`: rewrite the "use canonical as anchor" section.
  Require grammatical fit to the question. Specify: when the canonical begins with
  "No," or "Yes," and the question is not a yes/no question, drop the polarity word
  and reshape into a statement.
- Optional minor JSON edits: lightly rewrite 2–3 canonicals whose shape is most
  brittle (candidates: `pain_temporal`, `pain_exacerbating`, `pain_relieving`,
  `assoc_fever`, `assoc_urine_blood`, `assoc_urine_frequency`, `assoc_bowels`,
  `assoc_trauma`). Keep clinical content identical; rephrase only.

**Success criteria:**
- "How has the pain changed since it started?" produces a natural answer that does
  not begin with "No," — e.g. "It's been pretty steady, hasn't really got better
  or worse since it started."
- "Is there anything that makes the pain worse?" still produces a clean canonical
  ("No, nothing makes it worse" is fine because the question was yes/no).
- No clinical content drift: spot-check 5 facts pre/post — meaning identical.

---

### Task 4 — Open-question budget across scopes (Issue 1)

**Scope:**
- `prompts/intent_agent_system.md`: replace the "repeat broad prompts in the same
  domain stop releasing new facts" rule with a scope-progression rule. Define a
  budget of 3 broad-open releases across distinct scopes (pain-history surface,
  associated-symptoms surface, maybe ICE surface), in that order. After all scopes
  have fired their broad-release, further broad opens deflect ("you'd need to ask me
  about something specific"). The student can ALWAYS still get content via specific
  questions.
- `facts/michael_doyle.json`: expand the associated-symptoms surface set. Mark
  `assoc_nausea` and `assoc_dysuria` as also unlocking on a broad
  associated-symptoms ask (currently only `assoc_vomiting`). Update their
  `when_to_disclose` strings accordingly.
- `prompts/disclosure_few_shots.yaml`: add 3 new few-shots covering the broad-open
  progression: open #2 (pain trio), open #3 (associated symptoms surface set), open
  #4 (deflection — "you'd need to ask me about something specific").
- `prompts/patient_agent_system.md`: ensure the "multiple facts in one turn" guidance
  scales gracefully to 3-fact and 4-fact disclosures (currently says 2–3 sentences;
  may need to allow 3–4).

**Success criteria:**
- After "What brings you in today?" → "Tell me a bit more about that" →
  "Tell me more about the pain" → "Anything else been going on?" → "Any other
  symptoms?": each turn yields a distinct, openish response covering the next scope.
- A 5th broad open ("Anything else?") produces a natural deflection, not silence and
  not invention.
- A specific question (e.g. "Does it spread anywhere?") at any point still unlocks
  the targeted protected fact regardless of how many broad opens have already fired.

---

### Task 5 (optional, post-fixes) — Add `social_smoking_cessation` fact

Surfaced during prior testing (the "have you considered quitting smoking" leak).
Adding this small fact closes a known authoring gap and makes the patient's response
deterministic and consistent.

**Scope:**
- `facts/michael_doyle.json`: add a new fact for cessation attempts.
- `prompts/disclosure_few_shots.yaml`: optional one-shot for the question.

**Success criteria:**
- "Have you considered quitting smoking?" produces a stable canonical-anchored response.

## Project Status Board

- [x] Task 1 — Fix recap dumping (Issue 3)
- [x] Task 2 — Add `social_chat` utterance type (Issue 2)
- [x] Task 3 — Tighten canonical-adherence prompt (Issue 4)
- [x] Task 4 — Open-question budget across scopes (Issue 1) — **awaiting human verification**
- [ ] Task 5 — (Optional) Add `social_smoking_cessation` fact

## Current Status / Progress Tracking

- **2026-05-23**: Plan drafted by Planner. Awaiting human approval to begin Task 1 in
  Executor mode.
- **2026-05-23**: Plan approved. Task 1 implemented:
  - `patient_agent.py`: signature changed to take `newly_authorised_facts` and
    `previously_disclosed_ids` separately.
  - `orchestrator.py`: stops merging earned-fact lists; hydrates only newly-earned to
    full fact objects; passes previously-earned as ids only.
  - `prompts/patient_agent_system.md`: rewrote "the most important rule" and
    "already-disclosed facts" sections; updated input-format docs to describe the new
    two-block structure; added explicit "do not recap unless asked to summarise" rule.
  - No linter errors. No remaining references to the old `available_facts` /
    `facts_available_to_disclose_this_turn` names in code or live prompts (only in
    historical narrative inside this plan doc).
  - Human approved progression to Task 2.

- **2026-05-23**: Task 2 implemented:
  - `prompts/intent_agent_system.md`: added new utterance type `social_chat` to the
    taxonomy. Added two new core rules: "Social / rapport check-ins unlock nothing
    clinically" (returns `newly_earned: []`) and "Social chat + clinical question =
    treat as the clinical question". Added a disambiguation tip distinguishing
    `social_chat` from `aspect_specific` (the latter targets a clinical aspect).
  - `prompts/disclosure_few_shots.yaml`: appended 3 new few-shots covering
    "Hi, how are you doing today?", "I'm sorry to hear that, how are you holding up?",
    and "Are you doing okay right now?". All return `newly_earned: []` with rationale
    explaining the social_chat classification.
  - `prompts/patient_agent_system.md`: the no-fact branch is now 3-way (filler_only /
    social_chat / clinical-with-no-fact). The social_chat branch allows brief
    persona-grounded emotional response (in pain, worried, rough) with explicit
    examples of acceptable and unacceptable outputs — unacceptable being any new
    clinical specifics (severity number, location, character, symptoms, timings, PMH,
    medications).
  - Human approved progression to Task 3.

- **2026-05-23**: Task 3 implemented:
  - `prompts/patient_agent_system.md`: replaced the one-line canonical-adherence rule
    with a structured rule. Distinguishes "clinical content" (immutable ground truth)
    from "grammatical shape" (must fit the question). Worked examples for the
    polarity-opener case ("No,…" canonical with non-yes/no question → drop "No,").
    Allows short natural connectors. Hard rules retained: no embellishment, no new
    clinical content, meaning fixed.
  - `facts/michael_doyle.json`: lightly rewrote 4 canonicals to lead with meaning
    rather than polarity, so the patient agent can prepend "No," for yes/no asks but
    use the canonical as-is for open asks. Clinical content unchanged in all four:
      - `pain_temporal`: "It's been consistently bad since it started, it hasn't
        really got any better or worse." (was: "No it's definitely not getting…")
      - `pain_exacerbating`: "Nothing really makes it worse that I've noticed."
        (was: "No, nothing makes it worse.")
      - `assoc_urine_frequency`: "I haven't noticed any changes in when I go to pee,
        I've not been going more or less I don't think." (dropped "No")
      - `assoc_bowels`: "My bowels are normal for me - I'm usually a bit constipated,
        but I opened my bowels yesterday as normal." (dropped "No")
  - JSON validated (40 facts, all ids unique).
  - Human approved progression to Task 4.

- **2026-05-23**: Task 4 implemented:
  - `intent_agent.py`: gate's user prompt now includes `TOTAL TURNS SO FAR: {n}` so
    the late-turn ICE rule can be expressed as a turn-count threshold.
  - `prompts/intent_agent_system.md`: replaced the "repeat broad prompts in same
    domain stop releasing" rule with a 5-step **Broad-open progression across scopes**
    rule:
      1. PC duration brief
      2. Pain-history surface trio
      3. Associated-symptoms surface pair (vomiting + nausea)
      4. ICE concerns (only when TOTAL TURNS SO FAR >= 10; only `ice_concerns_main`)
      5. Exhaustion → return `newly_earned: []` with `utterance_type: broad_open`.
     Added explicit "specific questions still always work" carve-out.
  - `prompts/disclosure_few_shots.yaml`: updated 3 existing few-shots to align with
    the new rule (the assoc-symptoms broad now releases the pair; the previously
    "repeat broad → []" example now releases nausea; the previously "anything else
    inside pain-history → []" example now progresses to associated surface). Added
    4 new few-shots: early broad-skip-to-associated, late-turn ICE concerns release,
    full-exhaustion broad return-empty, and ambiguous "Tell me more" walking to
    pain-history surface trio.
  - `facts/michael_doyle.json`: updated `when_to_disclose` text on `assoc_vomiting`,
    `assoc_nausea`, and `ice_concerns_main` to encode the new pairing and the
    late-turn rule. Canonical wording unchanged in all three.
  - `prompts/patient_agent_system.md`: added a fourth sub-bullet to the no-fact
    branch for `utterance_type: broad_open` exhaustion - patient gives a soft
    "ask me something specific" deflection rather than the generic "I don't know"
    deflection.
  - JSON + YAML validated; all fact ids referenced in few-shots are real; no
    linter errors.
  - **Awaiting manual verification by the human against Task 4 success criteria.**

## Decisions from the human (2026-05-23)

1. **ICE on broad open** — Allowed *late* in the consultation only. Specifically: after
   ~10 turns, a broad-open prompt may surface `ice_concerns_main` (the general "I'm
   worried it's something serious" line). All other ICE facts (`ice_ideas`,
   `ice_concerns_why_surgery`, `ice_expectations`) remain direct-question-only at all
   times. The 10-turn threshold uses `len(history)` as the proxy.
2. **Associated-symptoms broad set** — `{assoc_vomiting, assoc_nausea}` for Michael
   Doyle. Not dysuria. Note that the broad-release set is case-specific; future cases
   should pick their own based on the clinical history.
3. **Light canonical rewrites in Task 3** — Approved.
4. **Execution order** — Approved as 3 → 2 → 4 → 1 → 5.

(Originally numbered 1-4 by Issue; in execution order this is Task 1, 2, 3, 4, 5.)

## Executor's Feedback or Assistance Requests

- **2026-05-23**: Plan approved with the four decisions above. Beginning Task 1
  (recap-dumping fix, formerly Issue 3). Will pause at the success-criteria checkpoint
  for manual verification before continuing.

## Lessons

(empty — to be filled as we encounter and fix bugs during execution)

# OSCE Prototype — "Mm" Failure Mode

## Background and Motivation

A test session with the Michael Doyle case surfaced a recurring failure mode: the patient
agent replies with a minimal "Mm." in turns where a real patient would clearly say
"Yeah, that's right" or "Sure, go ahead." The student-facing effect is that the
conversation feels broken at exactly the moments where the student is doing two
clinically important things:

1. **Summarising / reading back** what the patient has said and asking for confirmation
   ("Okay, so the pain is on your right side… is that right?").
2. **Signposting** the next part of the consultation and asking permission to proceed
   ("I'm going to ask you a few more questions about the pain. Will that be ok?").

The previous clinical-review fixes (`.cursor/clinical-review-fixes.md`, Tasks 1–4
completed) corrected four other classes of issue (recap dumping, social-chat
mis-classification, canonical adherence, broad-open budget). This failure mode is
adjacent to but distinct from them and is not addressed by any of those fixes.

The architecture itself is sound; the bug is concentrated in the **intent agent's
utterance-type taxonomy** and the **patient agent's prompt mapping** for the
`filler_only` branch.

## Key Challenges and Analysis

### Observed "Mm." turns in the failure log

Pulled from the meeting's chat log. The list below is exhaustive for that session,
grouped by the kind of student utterance that triggered the "Mm." response. Each row
notes the most likely classification path through the current intent-agent taxonomy.

| # | Student utterance | What it actually is | Likely current classification |
|---|---|---|---|
| 1 | "All right, so you're having this pain in the right side, is that [cut off]" | Confirmation / readback | `filler_only` (leading "All right, so…" + no new fact) |
| 2 | "Okay, so the pain is on your right side, around the back. It starts really suddenly, just before you had lunch, and it came out of absolutely nowhere. Is that right?" | Summary + confirmation request (closed yes/no) | `filler_only` (same pattern) |
| 3 | "Ok, no problem. I'm going to ask you a few more questions about the pain. Will that be ok?" | Procedural signposting + consent-to-proceed (closed yes/no) | `filler_only` |
| 4 | "Okay, good, fine. I'm going to ask you a few more questions about a few other symptoms that you might be having. Is that okay?" | Procedural signposting + consent-to-proceed | `filler_only` |
| 5 | "Can you go away?" | Off-script / non-clinical / arguably abusive | `filler_only` or `unclear` |
| 6 | "Ok, so I've asked you lots of questions about your condition… I've got a few more questions to ask you about your background. Would that be ok" | Procedural signposting + consent-to-proceed | `filler_only` |
| 7 | "What do you mean that you don't know?" | Clarification request (after the patient said "I don't really know" to "have you ever been admitted") | `filler_only` or `unclear` |
| 8 | "Okay, and where do you guys live?" | Clinical question — no matching fact in the store (the closest is `social_living` "I live with my girlfriend", already disclosed) | `filler_only` (mis-classified) or `aspect_specific` with no unlock |
| 9 | "Okay, that's fair enough. If you ever think that you do want to explore this further, please, of course, let us know, and there's lots of services and information that I can give you to help you in this process." | Long non-question / meta-statement / closing-ish | `filler_only` (genuinely no question to answer) |

Note that **one** very similar utterance in the same session did work correctly:

> Student: "Just to summarize what you've told me so far, you've been having this pain
> for about 24 hours. It came on very suddenly on the right side… Is that all correct?"
>
> Patient: "Yeah, that's about right. It's on the right side. Thanks."

This is striking: the gate handled a long summary-readback fine, but failed on the
shorter, more directly worded "Is that right?" readback two turns earlier. The
difference is most likely that the **classifier weights leading filler markers too
heavily** ("Okay, so…", "All right, so…") and pushes the whole utterance into
`filler_only`, whereas the longer "Just to summarize…" version contains enough
clinical content (re-stated facts) that the classifier resists the filler path.

### Where the "Mm." literally comes from

In `patients/michael_doyle/patient_system.md`, line 30:

> If `utterance_type` is `filler_only` (e.g. "okay", "right"), stay essentially silent
> — reply with at most a brief sound like **"Mm"** or just wait. Do not add information.

So "Mm." is the **intentional and explicit** instruction for `filler_only` utterances.
The bug is upstream: the intent agent is **mis-classifying non-filler utterances** as
`filler_only`, and the patient agent obediently emits "Mm."

### Why mis-classification happens: a taxonomy gap

The current taxonomy in `patients/michael_doyle/intent_system.md` has **eight**
utterance types: `filler_only`, `social_chat`, `broad_open`, `aspect_specific`,
`specific_direct`, `yes_no`, `unclear`, `closing`.

None of these fit two common, distinct, **non-clinical** communication acts in a
history-taking consultation:

1. **Confirmation / readback request.** The student restates one or more
   already-disclosed facts and asks the patient to confirm.
   Examples: *"So the pain is on the right side, is that right?"*, *"That came on
   suddenly, did it?"*, *"Just to check — twenty-four hours, right?"*
   - Not `filler_only`: it contains a real question.
   - Not `yes_no`: the taxonomy describes `yes_no` as new clinical closed questions
     ("do you smoke", "any allergies") that **unlock a fact**. Readbacks unlock nothing
     because the content has already been disclosed.
   - Not `aspect_specific` / `specific_direct`: those target an unasked fact.
   - Not `social_chat`: it's about the case, not about the patient as a person.

2. **Procedural / signposting / consent-to-proceed.** The student tells the patient
   what they're about to do and asks permission.
   Examples: *"I'm going to ask you a few questions about your symptoms — is that
   okay?"*, *"Would you mind if I asked about your family history?"*, *"Can I take you
   through some background questions?"*
   - Not `filler_only`: it contains a real (procedural) question.
   - Not `yes_no` in the current taxonomy's sense (no clinical fact to unlock).
   - Not `social_chat`: it's about the consultation procedure, not the patient's
     feelings.
   - Not `closing`: the student is moving on to the next section, not wrapping up.

With no fitting bucket and an explicit "lean toward releasing nothing when borderline"
instruction in the intent system prompt, the classifier defaults to the safest empty
bucket — which is `filler_only`. The patient agent then renders the "Mm."

### Compounding factors

**Factor A — leading discourse markers pull utterances into `filler_only`.**
"Okay,", "Ok,", "All right,", "So,", and "Right," at the start of an utterance look
filler-like. The current rule says *"Filler + question = treat as the question"*
(line 24 of `intent_system.md`), but the rule is implicitly framed around **clinical**
questions ("Right, do you smoke?" → process smoking question). For procedural and
confirmation follow-ups, the body is itself non-clinical, so the rule doesn't
strongly redirect the classifier.

**Factor B — "Mm." is a stiff fallback even when `filler_only` is correct.**
For genuinely empty acknowledgements ("Okay.", "Right."), the patient really should
say very little. But the literal output "Mm." reads as robotic and identical every
time, which contributes to the "broken-feeling" subjective impression even on the
turns where the classification is technically right (e.g. row 9 in the table above is
not really a question and arguably should produce a near-silent response — but the
identical "Mm." across multiple turns amplifies the sense of failure).

**Factor C — the off-script case ("Can you go away?") has no good handler.**
This is rare and arguably out-of-scope for clinical realism, but the current handling
("Mm.") is poor. A real patient would say something like "Sorry?" or simply not
respond. It would be reasonable to leave this as a long-tail issue rather than
designing for it.

**Factor D — the "Where do you guys live?" row is a different failure with the same surface.**
This one is a *clinical* question for which the case has **no authored fact** (the
fact store only contains `social_living` = "I live with my girlfriend", and the
student already earned that on the prior turn). The correct behaviour is the
"Otherwise" deflection branch in the patient prompt: *"I don't really know" / "I
haven't noticed anything like that."* Instead the gate appears to classify it as
`filler_only` (probably because of the leading "Okay, and…"), routing to "Mm." The
underlying fix is the same — stop letting leading filler markers drag a real question
into the filler bucket.

### Root cause (single-sentence summary)

The intent agent's utterance-type taxonomy lacks dedicated categories for
**confirmation/readback** and **procedural/consent-to-proceed** student utterances,
which causes the classifier to fall back to `filler_only` for those acts (especially
when they begin with discourse markers like "Okay," / "So," / "All right,"). The
patient agent then deterministically renders "Mm." per its prompt — producing the
observed failure.

A secondary contributor is that the `filler_only` response itself is hard-coded to
"Mm.", which feels robotic even on turns where filler classification is correct.

### Chosen solution direction — "Option 2+"

The human has selected a combined approach that **eliminates the literal string
"Mm" from the system entirely**, rather than re-skinning it with another token like
"Mhm". The reasoning: even an alternate filler token would still read as a robotic
noise where a real human listener would simply not speak. The fix is structural,
not cosmetic.

The approach has two parts.

#### Part A — Route the currently-misclassified utterances to proper affirmatives

Adds two new utterance types to the intent gate, both of which return
`newly_earned: []` but tell the patient agent to give a brief in-character
affirmative response.

1. **`confirmation_request`** — student restates already-disclosed facts and asks
   the patient to confirm them. Examples:
   - *"…is that right?"*
   - *"So, twenty-four hours, did you say?"*
   - *"Just to check — right side, yeah?"*

   Patient routing: brief affirmative anchored on whether the readback is accurate.
   Common forms: *"Yeah, that's right."* / *"Yep, exactly."* / *"That's it."* If the
   readback contains an error, the patient may gently correct using only
   already-disclosed content (e.g. *"Actually it was the right side, not the
   left."*). The patient agent already has conversation history visible, so it can
   verify the readback.

2. **`procedural`** — student is signposting the next section of the consultation
   and asking permission to proceed. Examples:
   - *"I'm going to ask you a few more questions about your symptoms — is that
     okay?"*
   - *"Would you mind if I asked about your background next?"*
   - *"Can I take you through some questions about your medical history?"*

   Patient routing: brief affirmative consent. Common forms: *"Yeah, that's fine."*
   / *"Sure, go ahead."* / *"Of course."* / *"Yeah, no problem."*

Add 4–6 few-shots to `disclosure_few_shots.yaml`, including variants with leading
discourse markers ("Okay, so…", "All right, so…", "Ok,…") so the classifier learns
not to be pulled into `filler_only` by the opening filler word.

After Part A, the only utterances that still hit `filler_only` are **standalone
fillers with no question at all** — *"Okay."* / *"Right."* / *"I see."* — which are
genuinely rare in a real consultation.

#### Part B — For residual `filler_only`, the patient agent is never called

This is the structural change that fully removes the "Mm" token from the system.

1. **`orchestrator.py`**: when `intent_result["utterance_type"] == "filler_only"`,
   short-circuit before calling `patient_agent.respond()`. Return
   `patient_response = ""`. The patient agent is never invoked on those turns, so
   it cannot emit "Mm".

2. **`app.py`**: when the patient response is empty:
   - Skip the TTS call (no audio synthesis on filler turns).
   - Render the turn with the student's bubble as normal, **followed by a faint
     italic placeholder** under it — text like *"(Michael acknowledges silently)"*
     — so it's transparent to the student that the turn was processed and the
     patient simply did not have anything to say. (Human chose this over a fully
     hidden patient bubble.)
   - Store the turn in `st.session_state.history` with `patient: ""` so the
     conversation chronology is preserved.

3. **Agent context filtering**: when `intent_agent._build_history_block` and
   `patient_agent.respond` build their history context for the *next* turn, they
   should skip any prior turn where `patient` is empty. This prevents the empty
   patient response from polluting the LLM's view of the conversation. The
   classifier will then see the conversation as if the filler turn never happened,
   which matches how humans actually parse back-channels.

4. **`patient_system.md`**: remove the `filler_only` branch from the patient
   prompt (since the patient agent is no longer called for those turns). The
   patient prompt is then only responsible for turns where there is something
   to actually say.

5. **Markdown export (`format_session_as_markdown`)**: skip turns with empty
   patient response, so the downloadable transcript matches what was actually
   said.

#### Part C — Off-script handling (small additional fix)

The "Can you go away?" off-script case currently sometimes lands in `filler_only`
and produces "Mm." To make this reliably hit the existing `unclear` deflection
branch ("Sorry, I'm not sure what you mean.") instead, add one few-shot example
to `disclosure_few_shots.yaml` showing an off-script / rude utterance classified
as `unclear` with `newly_earned: []`. No code changes; this is purely a
classifier nudge.

### Out of scope

- The *"Okay, and where do you guys live?"* case is a real clinical question for
  which no fact exists. Part A will help (the leading "Okay, and…" won't pull it
  into `filler_only`), but the residual answer is the existing deflection branch
  ("I don't really know"). If a real answer is wanted, a `social_address` fact
  must be authored — that is an authoring decision, not a fix for this failure
  mode.
- Voice/TTS behaviour. The fix is at the text-generation layer; TTS follows
  whatever the patient agent produces (or, on silent turns, is not called at all).
- Any change to patient persona or already-authored facts content. This is purely
  a classification + response-routing + UI fix.

## High-level Task Breakdown

Each task is sized small and includes its own success criteria. Per the Planner /
Executor workflow, the Executor should complete one task at a time and pause for
human verification before moving on.

---

### Task 1 — Add `confirmation_request` and `procedural` utterance types (Part A)

**Scope:**
- `patients/michael_doyle/intent_system.md`:
  - Extend the "Utterance type taxonomy" section with the two new types and a
    clear definition for each, including disambiguation from `filler_only`,
    `yes_no`, and `social_chat`.
  - Add a "Disambiguation tip" noting that confirmation/readback questions and
    procedural questions are NOT pulled into `filler_only` by leading discourse
    markers.
- `patients/michael_doyle/patient_system.md`:
  - Add two new sub-bullets to the no-fact branch (the section starting with
    "If the `<newly_authorised_this_turn>` block says `(none)`"), one for
    `confirmation_request` and one for `procedural`. Each describes the expected
    response style with 2–3 acceptable example phrasings and one unacceptable
    example (so the model doesn't volunteer new clinical content).
- `patients/michael_doyle/disclosure_few_shots.yaml`:
  - Add 4 few-shots: 2 confirmation_request examples (one short, one a longer
    summary readback) and 2 procedural examples (different signposting forms).
  - Include leading-"Okay, so…" / leading-"All right, so…" variants to teach the
    classifier the discourse-marker pattern.

**Success criteria:**
- "Okay, so the pain is on your right side… is that right?" → gate classifies as
  `confirmation_request`, `newly_earned=[]`; patient says something like "Yeah,
  that's right." (no "Mm", no recap dumping, no new clinical content).
- "I'm going to ask you a few more questions about the pain. Will that be ok?" →
  gate classifies as `procedural`, `newly_earned=[]`; patient says something like
  "Yeah, that's fine." or "Sure, go ahead."
- Genuine filler ("Okay.", "Right.") still classifies as `filler_only`. (The
  observable patient behaviour for filler_only changes in Task 2; in Task 1 it
  may still produce "Mm" — that's expected and gets removed in Task 2.)
- "Just to summarize…" long readbacks (which already worked) still work — no
  regression on previously-correct behaviour.

---

### Task 2 — Suppress the patient turn entirely for `filler_only` (Part B)

**Scope:**
- `orchestrator.py`: when `utterance_type == "filler_only"`, short-circuit
  before calling `patient_agent.respond`. Return `patient_response = ""`.
- `intent_agent.py` (`_build_history_block`) and `patient_agent.py` (history
  construction in `respond`): filter out turns where `patient` is an empty
  string, so the agents never see empty-response turns in their context window.
- `app.py`:
  - Skip the TTS call when `result["patient_response"]` is empty.
  - In the conversation renderer, when a turn has an empty patient response,
    show the student's chat bubble as normal followed by a faint italic
    placeholder (e.g. `_(Michael acknowledges silently)_`) instead of a patient
    chat bubble.
  - Apply the same rule when rendering archived sessions.
- `format_session_as_markdown` in `app.py`: skip any turn where `patient` is
  empty when emitting the conversation block. Empty turns don't appear in the
  downloadable transcript.
- `patients/michael_doyle/patient_system.md`: remove the `filler_only` sub-bullet
  from the no-fact branch (since the patient agent is no longer invoked on those
  turns). Update the "Input format you'll receive" section if needed.

**Success criteria:**
- A standalone "Okay." from the student → no patient bubble appears, faint
  italic *"(Michael acknowledges silently)"* shown under the student's message,
  no TTS audio generated.
- The next student question after a silent turn behaves normally: the gate and
  the patient agent don't reference the silent turn in their context (verified
  by inspecting the rationale on the next turn — it should not allude to the
  filler turn).
- The downloaded `.md` transcript contains no empty patient turns.
- Live and archived session renderers both behave consistently.
- Confirmation_request and procedural turns from Task 1 still produce a real
  patient response (this is not affected — they're not `filler_only`).

---

### Task 3 — Off-script few-shot (Part C)

**Scope:**
- `patients/michael_doyle/disclosure_few_shots.yaml`: add one few-shot showing
  an off-script / non-clinical utterance (e.g. "Can you go away?") classified as
  `unclear` with `newly_earned: []`.

**Success criteria:**
- "Can you go away?" → gate classifies as `unclear`; patient produces the
  existing deflection ("Sorry, I'm not sure what you mean.") rather than a
  silent turn or "Mm.".

---

### Task 4 (verification) — End-to-end replay of the failure session

Not a code task — a manual verification. Re-run a conversation that mirrors the
failure log:
1. Opening + pain history (build up earned facts).
2. Confirmation/readback: *"Okay, so the pain is on your right side… is that right?"*
3. Procedural: *"I'm going to ask you a few more questions about the pain. Will that be ok?"*
4. Pure filler: *"Okay."* alone.
5. Off-script: *"Can you go away?"*

**Success criteria:**
- Step 2 produces a brief affirmative — not "Mm".
- Step 3 produces a brief consent affirmative — not "Mm".
- Step 4 produces no patient bubble, faint silent-acknowledgement indicator
  shown.
- Step 5 produces the existing "Sorry, I'm not sure what you mean." deflection.
- The word "Mm" (or "Mm." with punctuation) does not appear anywhere in the
  patient's outputs during the session.

## Project Status Board

- [x] Root-cause analysis written and reviewed by the human
- [x] Fix direction approved — Option 2+ (eliminate "Mm" entirely via routing +
      structural suppression of filler_only turns)
- [x] Task 1 — Add `confirmation_request` and `procedural` utterance types (Part A)
      — **awaiting human verification**
- [ ] Task 2 — Suppress patient turn for `filler_only` (Part B)
- [ ] Task 3 — Off-script `unclear` few-shot (Part C)
- [ ] Task 4 — End-to-end verification against the failure log

## Current Status / Progress Tracking

- **2026-05-25**: Planner mode. Root-cause analysis drafted from the failure log
  shared in chat. Initial three options presented.
- **2026-05-25**: Human selected Option 2 with the explicit constraint that "Mm"
  must be removed entirely (not re-skinned with another token). Plan revised to
  "Option 2+": Part A routes mis-classified utterances to affirmatives, Part B
  short-circuits the patient agent for residual `filler_only`, Part C nudges
  off-script utterances to `unclear`.
- **2026-05-25**: Human decisions captured —
  1. Silent-turn UI: faint italic placeholder *"(Michael acknowledges silently)"*
     under the student's bubble (rather than fully hidden or empty bubble).
  2. Off-script ("Can you go away?"): few-shot to push the gate toward
     `unclear`, which already deflects naturally.
- **2026-05-25**: Awaiting human approval to switch to Executor mode and begin
  Task 1.
- **2026-05-25**: Task 1 implemented:
  - `patients/michael_doyle/intent_system.md`:
    - Added 3 new core rules: "Confirmation / readback requests unlock nothing",
      "Procedural / consent-to-proceed unlocks nothing", and "Leading discourse
      markers do not make an utterance filler".
    - Added 2 new utterance types to the taxonomy: `confirmation_request` and
      `procedural`. Tightened the definitions of `filler_only` ("standalone only,
      no question attached"), `yes_no` ("about a clinical aspect… typically
      unlocks a fact"), and `unclear` ("or off-script utterance the patient can't
      reasonably answer").
    - Added 3 new disambiguation tips: confirmation_request vs filler_only,
      procedural vs filler_only and yes_no, confirmation_request vs closing.
  - `patients/michael_doyle/patient_system.md`:
    - Added 2 new sub-bullets to the no-fact branch covering
      `utterance_type: confirmation_request` (brief affirmative anchored on
      conversation history; may gently correct using already-disclosed content;
      explicit "do NOT use 'Mm'" instruction) and `utterance_type: procedural`
      (brief affirmative consent; explicit "do NOT use 'Mm'" instruction).
    - The `filler_only` bullet is unchanged in Task 1 (still maps to "Mm"); it
      will be removed entirely in Task 2 when the orchestrator stops calling the
      patient agent for filler turns.
  - `patients/michael_doyle/disclosure_few_shots.yaml`:
    - Added 4 new few-shots: 2 confirmation_request examples (one with leading
      "Okay, so…" + readback, one short "Just to check…" tag question) and 2
      procedural examples (one bare consent-to-proceed, one with leading
      "Ok, so…").
  - YAML parses (27 few-shots total). No linter errors.
  - **Awaiting manual verification by the human against Task 1 success criteria
    before proceeding to Task 2.**

## Executor's Feedback or Assistance Requests

- **2026-05-25**: Task 1 complete and ready for manual verification.
  Suggested checks in the Streamlit app against the live Michael Doyle case:
  1. Build up some pain history (e.g. "What brings you in?" → "Tell me more
     about the pain.").
  2. Ask the failing confirmation question: *"Okay, so the pain is on your
     right side and it came on suddenly out of nowhere — is that right?"*
     Expect: gate classifies `confirmation_request`, patient replies with a
     brief affirmative like "Yeah, that's right." (NOT "Mm.").
  3. Ask the failing procedural question: *"I'm going to ask you a few more
     questions about the pain. Will that be ok?"* Expect: gate classifies
     `procedural`, patient replies with something like "Yeah, that's fine."
     (NOT "Mm.").
  4. Standalone *"Okay."* alone → should still classify as `filler_only` and
     still emit "Mm." for now. That residual "Mm" gets removed in Task 2.
  5. Long readback *"Just to summarize what you've told me so far… is that all
     correct?"* should continue to work as before (regression check).
  
  Note: the patient agent's `temperature=0.7` plus model variability means the
  exact wording may differ between runs — what matters is that the response is
  a brief affirmative, not "Mm", and contains no new clinical content.

## Lessons

(empty — to be filled during implementation)

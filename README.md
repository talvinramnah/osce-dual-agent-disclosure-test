# Bleep64 OSCE Prototype - Disclosure-Gated Patient Agent

Two-agent architecture for OSCE patient simulation. The goal: test whether decoupling intent classification from response generation reduces the information-leakage problem we currently see in single-prompt voice agents.

## The hypothesis

Current OSCE agents (built as a single LLM prompt in ElevenLabs) leak protected clinical information because the entire case sits in the LLM's context. When the student asks a broad open question, the LLM, trained to be helpful, gives away facts that should only unlock on specific, on-target questions. That destroys the realism and the marking opportunity.

This prototype splits the problem in two:

1. **Intent agent** sees the full fact store and decides which fact IDs the student has earned this turn based on how they asked. It outputs structured JSON. It never generates patient speech.
2. **Patient agent** receives only the facts that have been released and renders them as Michael Doyle would speak. It has no access to the full case.

The patient agent cannot leak what it does not know.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env               # then fill in OPENAI_API_KEY and ELEVENLABS_API_KEY
streamlit run app.py
```

The Streamlit UI opens with a chat on the left and a debug panel on the right showing exactly which facts the intent agent released each turn and why. You can type questions or use the mic input (Whisper transcribes). The patient response is synthesised through the configured ElevenLabs voice and embedded in the chat for playback.

## Project layout

```
.
├── app.py                              Streamlit UI
├── orchestrator.py                     wires intent + patient agents per turn
├── intent_agent.py                     intent + disclosure decision (LLM)
├── patient_agent.py                    patient response generation (LLM)
├── stt.py                              OpenAI Whisper wrapper
├── tts.py                              ElevenLabs TTS wrapper
├── requirements.txt
├── .env.example
├── facts/
│   └── michael_doyle.json              fact store - clinical case as discrete facts
└── prompts/
    ├── intent_agent_system.md          intent agent system prompt
    ├── patient_agent_system.md         patient persona prompt (no clinical facts)
    └── disclosure_few_shots.yaml       few-shot examples for the intent agent
```

## How the disclosure gate works

Each fact in `facts/michael_doyle.json` has:

- `id` - stable string identifier
- `domain` - groups facts (pain_history, associated_symptoms, ice, pmh, etc.)
- `canonical_response` - the exact wording the patient should anchor to when disclosing this fact
- `when_to_disclose` - natural-language unlock rule that the intent agent reads to decide whether the student's question earns it
- `protected` - boolean; protected facts only unlock on specific direct questions and never on broad expansion prompts

For each student utterance the intent agent receives the full fact index, the few-shot examples, the recent conversation, and the list of already-earned fact IDs. It returns:

```json
{
  "newly_earned": ["pain_radiation"],
  "rationale": "Student asked specifically about radiation - unlocks the protected radiation fact.",
  "utterance_type": "specific_direct"
}
```

The orchestrator then hands the patient agent only those fact objects (plus previously earned ones, so the patient can naturally reference earlier disclosures). The patient agent has zero awareness of facts that haven't been released.

## Editing the case

Everything Shree or any clinical reviewer might want to change lives outside the Python:

- `facts/michael_doyle.json` - add, remove, or change facts. Edit `canonical_response` to fine-tune how Michael says things. Edit `when_to_disclose` to refine the unlock rule. Toggle `protected`.
- `prompts/disclosure_few_shots.yaml` - when you find a phrasing the intent agent gets wrong, add it as a new example showing what should and should not unlock.
- `prompts/intent_agent_system.md` and `prompts/patient_agent_system.md` - edit the system prompts.

No code changes needed for content edits. Restart Streamlit (or clear the cache) to pick up changes.

## Comparing against the one-shot prompt

To vibe-check against the current single-prompt Michael Doyle agent, run the same student questions against both:

1. The current ElevenLabs voice agent (or paste the existing prompt into any LLM playground).
2. This Streamlit prototype.

Watch the protected facts in particular: **radiation** (groin), **character** (squeezing/stabbing), and **constancy** (waves). The single-prompt approach tends to leak these on broad expansion prompts. The disclosure-gated approach should not - if it does, the failure is now diagnosable (the rationale tells you why) and fixable (edit the fact's `when_to_disclose`, add a counter-example to the few-shots, or tighten the intent agent prompt).

## Model choice

- Intent agent: `gpt-4o-mini` - cheap, fast, classification + structured JSON is easy.
- Patient agent: `gpt-4o` - acting quality matters more here.

Both are set at the top of `app.py`. Swap to other models (Gemini 2.5, Claude Sonnet 4.6, DeepSeek) by changing those constants and updating the client in `intent_agent.py` / `patient_agent.py`.

## Known limitations of this prototype

- Streamlit, not real-time voice infrastructure. Voice round-trip is roughly 3-6 seconds typical (Whisper STT + intent LLM + patient LLM + ElevenLabs TTS). Acceptable for prototype vibe-checking, far too slow for production OSCE. Production needs Pipecat or LiveKit with streaming.
- No interruption handling, no barge-in, no streaming TTS.
- One case (Michael Doyle). Adding a second case = another JSON in `facts/` plus a case selector in the UI.
- No formal evals - this is for qualitative vibe checks against the current approach. Formal evals (golden datasets, LLM-as-judge against clinician scoring) come later.
- The intent agent is non-deterministic. The few-shot examples and clear `when_to_disclose` rules constrain it, but the same student question on different runs may occasionally produce different unlock decisions. Treat the rationale field as the debug surface when this happens.

## Where this goes if it works

1. Tighten the disclosure rules using real failure-mode examples from Shree's testing.
2. Port the orchestration into Pipecat with streaming STT (Deepgram) and streaming ElevenLabs TTS. This is roughly 200 lines of Pipecat boilerplate; the agents/, facts/, and prompts/ files migrate untouched.
3. Move the fact store out of JSON files and into Supabase, with an admin UI for clinical staff to author new cases.
4. Add formal evals: a golden dataset of student turns, expected unlock decisions, and clinician-marked patient response quality.

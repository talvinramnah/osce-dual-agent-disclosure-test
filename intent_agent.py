"""
Intent + Disclosure Agent for the OSCE patient simulation.

Decides which facts in the fact store the student has earned the right to
hear, based on the student's latest question and the conversation history.

This agent NEVER generates patient speech. It only outputs structured fact
IDs and metadata. Keeping clinical content off the patient agent's surface
is the whole point of the architecture.
"""

import json
import os
from typing import Any
from openai import OpenAI


class IntentAgent:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        system_prompt: str = "",
        few_shots: list[dict] | None = None,
        fact_store: dict | None = None,
    ):
        self.client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self.model = model
        self.system_prompt = system_prompt
        self.few_shots = few_shots or []
        self.fact_store = fact_store or {"facts": []}

    def _build_fact_index(self) -> str:
        """Render the fact store as a compact reference the LLM reads each turn."""
        lines = []
        for fact in self.fact_store["facts"]:
            protected = " [PROTECTED]" if fact.get("protected") else ""
            lines.append(
                f"- id: {fact['id']}\n"
                f"  domain: {fact['domain']}{protected}\n"
                f"  unlock_when: {fact['when_to_disclose']}"
            )
        return "\n".join(lines)

    def _build_few_shot_block(self) -> str:
        if not self.few_shots:
            return "(no examples provided)"
        rendered = []
        for ex in self.few_shots:
            rendered.append(
                f'Student question: "{ex["question"]}"\n'
                f"Already earned: {ex.get('already_earned', [])}\n"
                f"Newly earned: {ex['newly_earned']}\n"
                f"Rationale: {ex['rationale']}"
            )
        return "\n\n".join(rendered)

    def _build_history_block(self, history: list[dict]) -> str:
        if not history:
            return "(no prior turns yet)"
        recent = history[-5:]
        return "\n".join(
            f"Student: {turn['student']}\nPatient: {turn['patient']}" for turn in recent
        )

    def classify(
        self,
        question: str,
        history: list[dict] | None = None,
        already_earned: list[str] | None = None,
    ) -> dict[str, Any]:
        history = history or []
        already_earned = already_earned or []

        user_prompt = (
            f"FACT STORE:\n{self._build_fact_index()}\n\n"
            f"FEW-SHOT EXAMPLES:\n{self._build_few_shot_block()}\n\n"
            f"CONVERSATION HISTORY (last 5 turns):\n{self._build_history_block(history)}\n\n"
            f"TOTAL TURNS SO FAR: {len(history)}\n\n"
            f"ALREADY EARNED FACT IDS: {already_earned}\n\n"
            f'STUDENT\'S LATEST QUESTION: "{question}"\n\n'
            "Decide which facts the student has just earned, what type of utterance "
            "this was, and a one-sentence rationale. Return JSON only."
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        raw = response.choices[0].message.content
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {
                "newly_earned": [],
                "rationale": f"(could not parse intent agent output: {raw[:200]})",
                "utterance_type": "unclear",
            }

        # Defensive cleanup: never re-release an already-earned fact, and only
        # release fact IDs that exist in the store.
        valid_ids = {f["id"] for f in self.fact_store["facts"]}
        already_set = set(already_earned)
        result["newly_earned"] = [
            fid for fid in result.get("newly_earned", [])
            if fid in valid_ids and fid not in already_set
        ]
        return result

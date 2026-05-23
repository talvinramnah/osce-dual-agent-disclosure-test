"""
Patient Agent for the OSCE simulation.

Generates Michael Doyle's spoken response based ONLY on the facts the intent
agent has authorised this turn (plus facts already disclosed earlier in the
conversation). The patient agent never receives the full fact store, so it
cannot leak information it has not been given.
"""

import os
from openai import OpenAI


class PatientAgent:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        system_prompt: str = "",
    ):
        self.client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self.model = model
        self.system_prompt = system_prompt

    def respond(
        self,
        question: str,
        available_facts: list[dict],
        history: list[dict] | None = None,
        utterance_type: str = "",
    ) -> str:
        history = history or []

        if available_facts:
            facts_block = "\n".join(
                f'- {f["id"]}: "{f["canonical_response"]}"'
                for f in available_facts
            )
        else:
            facts_block = "(no facts available this turn - respond per persona rules: filler -> minimal, otherwise natural deflection)"

        history_messages: list[dict] = []
        for turn in history[-10:]:
            history_messages.append({"role": "user", "content": turn["student"]})
            history_messages.append({"role": "assistant", "content": turn["patient"]})

        current_user_msg = (
            f"<student_question>{question}</student_question>\n"
            f"<utterance_type>{utterance_type}</utterance_type>\n"
            f"<facts_available_to_disclose_this_turn>\n{facts_block}\n</facts_available_to_disclose_this_turn>"
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            *history_messages,
            {"role": "user", "content": current_user_msg},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.7,
        )

        return response.choices[0].message.content.strip()

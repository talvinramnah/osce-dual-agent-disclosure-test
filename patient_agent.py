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
        newly_authorised_facts: list[dict],
        previously_disclosed_ids: list[str] | None = None,
        history: list[dict] | None = None,
        utterance_type: str = "",
    ) -> str:
        history = history or []
        previously_disclosed_ids = previously_disclosed_ids or []

        if newly_authorised_facts:
            new_block = "\n".join(
                f'- {f["id"]}: "{f["canonical_response"]}"'
                for f in newly_authorised_facts
            )
        else:
            new_block = "(none)"

        if previously_disclosed_ids:
            prior_block = "\n".join(f"- {fid}" for fid in previously_disclosed_ids)
        else:
            prior_block = "(none)"

        # Silent turns (filler_only short-circuit) have an empty patient
        # response; skip them so the model sees a clean back-and-forth and
        # doesn't get confused by blank assistant messages.
        spoken_history = [turn for turn in history if turn.get("patient")]
        history_messages: list[dict] = []
        for turn in spoken_history[-10:]:
            history_messages.append({"role": "user", "content": turn["student"]})
            history_messages.append({"role": "assistant", "content": turn["patient"]})

        current_user_msg = (
            f"<student_question>{question}</student_question>\n"
            f"<utterance_type>{utterance_type}</utterance_type>\n"
            f"<newly_authorised_this_turn>\n{new_block}\n</newly_authorised_this_turn>\n"
            f"<already_disclosed_for_reference_only>\n{prior_block}\n</already_disclosed_for_reference_only>"
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

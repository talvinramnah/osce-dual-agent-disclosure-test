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

        history_messages: list[dict] = []
        for turn in history[-10:]:
            history_messages.append({"role": "user", "content": turn["student"]})
            history_messages.append({"role": "assistant", "content": turn["patient"]})

        response_instruction = ""
        if not newly_authorised_facts:
            if utterance_type == "broad_open":
                response_instruction = (
                    "<response_instruction>MANDATORY: No new facts this turn. "
                    "Reply with exactly ONE short sentence (max 12 words). "
                    "Do NOT repeat, summarise, paraphrase, or echo ANY clinical detail "
                    "from the conversation above — no pain location, timing, onset, "
                    "severity, or symptoms. "
                    "Sound like a patient who has nothing more to add on a vague prompt. "
                    "Good: \"I think that's everything I can think of.\" / "
                    "\"Not really, I've told you what I know.\" / "
                    "\"Was there something specific you wanted to ask?\" "
                    "Bad: re-listing pain details or saying \"like I said\" then repeating them."
                    "</response_instruction>\n"
                )
            elif utterance_type == "conversational_ack":
                response_instruction = (
                    "<response_instruction>MANDATORY: No new facts. The student made a "
                    "statement (reflection, empathy, or transition) — NOT a question. "
                    "Reply in 1-2 short sentences. You MUST say something (not silence). "
                    "You may confirm (\"Yeah, that's right.\") or respond to empathy "
                    "(\"Thanks, it's been rough.\"). Use only details already in the "
                    "chat — do NOT add new clinical facts and do NOT re-narrate your "
                    "full history."
                    "</response_instruction>\n"
                )
            elif utterance_type == "social_chat":
                response_instruction = (
                    "<response_instruction>No new clinical facts. "
                    "Brief emotional reply (1-2 sentences); do not add new symptoms."
                    "</response_instruction>\n"
                )
            elif utterance_type == "filler_only":
                response_instruction = (
                    "<response_instruction>No new facts. One-word backchannel only "
                    "(e.g. \"Mm.\" / \"Yeah.\" / \"Right.\")."
                    "</response_instruction>\n"
                )
            else:
                response_instruction = (
                    "<response_instruction>No new facts authorised. "
                    "One short deflection only — do NOT recap the history."
                    "</response_instruction>\n"
                )

        current_user_msg = (
            f"{response_instruction}"
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

        temperature = 0.4 if not newly_authorised_facts else 0.7

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )

        return response.choices[0].message.content.strip()

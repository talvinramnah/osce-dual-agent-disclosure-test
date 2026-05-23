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
import re
from typing import Any
from openai import OpenAI

# Heuristic broad-open detection (used to correct missed tier-2 pain unlocks).
_BROAD_OPEN_PATTERNS = (
    r"\btell me more\b",
    r"\banything else\b",
    r"\bwhat else\b",
    r"\bis there anything else\b",
    r"\bcan you (tell|describe|explain)\b",
    r"\bdescribe (it|that|the|your)\b",
    r"\bgo on\b",
    r"\bkeep going\b",
    r"\bwhat happened\b",
    r"\bwhat('s| is) been going on\b",
)

_ASSOC_STEER_PATTERNS = (
    r"\bother symptom",
    r"\bany other symptom",
    r"\banything else.*\b(body|symptom)\b",
    r"\banything else bothering you\b",
    r"\bapart from the pain\b",
    r"\bbesides the pain\b",
)

_SHORT_FILLER_PHRASES = frozenset({
    "ok", "okay", "k", "right", "yeah", "yep", "yup", "mm", "mm-hm", "mmhm", "mhm",
    "uh-huh", "uh huh", "i see", "go on", "got it", "sure", "alright", "all right",
    "fine", "thanks", "thank you", "cheers",
})

_CONVERSATIONAL_ACK_PATTERNS = (
    r"\bso you('ve| have)\b",
    r"\bso the (pain|problem)\b",
    r"\bokay,? so you\b",
    r"\bright,? so (the |you)",
    r"\bthat must be\b",
    r"\bthat sounds\b",
    r"\bi understand\b",
    r"\bi can see\b",
    r"\blet me (just )?(summar|recap)\b",
    r"\bwhat you('ve| have) (said|told|mentioned)\b",
    r"\byou('ve| have) (said|told|mentioned)\b",
    r"\bsounds like you\b",
    r"\bthank you for (telling|sharing|explaining)\b",
    r"\bi('m| am) sorry (to hear|about|that)\b",
    r"\bwe('ll| will) (come back|move on|ask)\b",
    r"\bi('d| would) like to ask\b",
)

_SPECIFIC_QUESTION_PATTERNS = (
    r"\bwhere\b.*\b(pain|hurt|ache)\b",
    r"\bwhen\b.*\b(start|begin|came on)\b",
    r"\bhow (bad|severe|intense)\b",
    r"\bout of ten\b",
    r"\b\d+\s*/\s*10\b",
    r"\bradiat",
    r"\bspread\b",
    r"\bmove\b.*\b(pain|anywhere)\b",
    r"\bfeel like\b",
    r"\bcharacter\b",
    r"\bsharp\b|\bdull\b|\baching\b|\bstabbing\b",
    r"\bconstant\b|\bcome(s)? and go",
    r"\bworse\b.*\b(breath|move|cough)\b",
    r"\bbetter\b",
    r"\bexacerbat",
    r"\breliev",
    r"\bsmok",
    r"\ballerg",
    r"\bmedicat",
    r"\bfever\b",
    r"\bvomit",
    r"\bnausea\b",
    r"\bdysuria\b",
    r"\bpee\b|\burinat",
    r"\bdo you\b",
    r"\bhave you\b",
    r"\bare you\b",
    r"\bdoes it\b",
    r"\bdid it\b",
)


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

    def _build_scope_status_block(self, already_earned: list[str]) -> str:
        """Deterministic hint so the LLM does not skip pain broad tier 2."""
        scopes = self.fact_store.get("broad_open_scopes")
        if not scopes:
            return ""

        earned = set(already_earned)
        lines = ["BROAD SCOPE STATUS (ordered - use for ambiguous broad opens):"]
        next_scope: dict | None = None

        for scope in scopes:
            facts = scope["facts"]
            requires = scope.get("requires", [])
            prereqs_met = all(r in earned for r in requires)
            complete = all(f in earned for f in facts)
            status = "complete" if complete else ("available" if prereqs_met else "blocked")
            lines.append(
                f"- {scope['id']}: {status} "
                f"(facts: {facts}, requires: {requires})"
            )
            if next_scope is None and prereqs_met and not complete:
                next_scope = scope

        if next_scope:
            missing = [f for f in next_scope["facts"] if f not in earned]
            steer = ""
            if next_scope.get("assoc_steer_only"):
                steer = " ONLY if the student clearly asks about other/associated symptoms."
            lines.append(
                f"NEXT AMBIGUOUS BROAD OPEN SHOULD UNLOCK: {missing} "
                f"(scope: {next_scope['id']}).{steer}"
            )
        else:
            lines.append(
                "NEXT AMBIGUOUS BROAD OPEN: nothing left in scope ladder "
                "(return newly_earned: [] and utterance_type: broad_open)."
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

    @staticmethod
    def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
        return any(re.search(p, text) for p in patterns)

    def _is_broad_utterance(self, question: str) -> bool:
        q = question.lower().strip()
        if not q:
            return False
        if self._matches_any(_SPECIFIC_QUESTION_PATTERNS, q):
            return False
        return self._matches_any(_BROAD_OPEN_PATTERNS, q)

    def _is_assoc_steer(self, question: str) -> bool:
        return self._matches_any(_ASSOC_STEER_PATTERNS, question.lower().strip())

    def _is_short_filler_only(self, question: str) -> bool:
        q = question.strip().lower().rstrip(".!?")
        if not q:
            return True
        if q in _SHORT_FILLER_PHRASES:
            return True
        words = q.split()
        if len(words) <= 2 and all(w in _SHORT_FILLER_PHRASES for w in words):
            return True
        return len(words) <= 3 and q in _SHORT_FILLER_PHRASES

    def _is_conversational_ack_statement(self, question: str) -> bool:
        """Full-sentence reflections / empathy / transitions — not questions, not短 filler."""
        text = question.strip()
        if not text or "?" in text:
            return False
        if self._is_short_filler_only(text):
            return False
        lower = text.lower()
        if re.match(
            r"^(what|where|when|why|how|who|do|does|did|have|has|had|are|is|was|were|"
            r"can|could|would|should|will|shall)\b",
            lower,
        ):
            return False
        if self._matches_any(_CONVERSATIONAL_ACK_PATTERNS, lower):
            return True
        return len(lower.split()) >= 6

    def _apply_conversational_ack_override(
        self, question: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._is_conversational_ack_statement(question):
            return result
        utterance = result.get("utterance_type", "")
        if utterance in ("filler_only", "unclear", "broad_open") and not result.get(
            "newly_earned"
        ):
            result["utterance_type"] = "conversational_ack"
            result["rationale"] = (
                "Full-sentence statement (reflection/empathy/transition) — "
                "no fact unlock; patient should give brief in-character ack."
            )
        return result

    def _next_incomplete_scope(
        self, already_earned: set[str]
    ) -> tuple[dict | None, list[str]]:
        scopes = self.fact_store.get("broad_open_scopes") or []
        for scope in scopes:
            requires = scope.get("requires", [])
            if not all(r in already_earned for r in requires):
                continue
            missing = [f for f in scope["facts"] if f not in already_earned]
            if missing:
                return scope, missing
        return None, []

    def _apply_broad_scope_override(
        self,
        question: str,
        already_earned: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Correct common LLM failure: skipping pain broad tier 2 before exhaustion."""
        scopes = self.fact_store.get("broad_open_scopes")
        if not scopes:
            return result

        earned = set(already_earned)
        utterance = result.get("utterance_type", "")
        is_broad = utterance == "broad_open" or self._is_broad_utterance(question)
        if not is_broad:
            return result

        next_scope, missing = self._next_incomplete_scope(earned)
        if not next_scope or not missing:
            return result

        if next_scope.get("assoc_steer_only") and not self._is_assoc_steer(question):
            pass
        elif next_scope.get("assoc_steer_only"):
            result["newly_earned"] = [f for f in missing if f not in earned]
            result["utterance_type"] = "broad_open"
            result["rationale"] = (
                f"Scope override ({next_scope['id']}): associated-symptoms broad steer."
            )
            return result

        if self._is_assoc_steer(question):
            return result

        newly = result.get("newly_earned", [])
        wrongly_assoc = {"assoc_vomiting", "assoc_nausea"} & set(newly)
        if wrongly_assoc and next_scope["id"] in ("pain_broad_2", "pc_duration", "pain_broad_1"):
            newly = []

        if not newly or wrongly_assoc:
            result["newly_earned"] = [f for f in missing if f not in earned]
            result["utterance_type"] = "broad_open"
            result["rationale"] = (
                f"Scope override ({next_scope['id']}): next broad tier unlocks {missing}."
            )

        return result

    def classify(
        self,
        question: str,
        history: list[dict] | None = None,
        already_earned: list[str] | None = None,
    ) -> dict[str, Any]:
        history = history or []
        already_earned = already_earned or []

        scope_block = self._build_scope_status_block(already_earned)
        scope_section = f"{scope_block}\n\n" if scope_block else ""

        user_prompt = (
            f"FACT STORE:\n{self._build_fact_index()}\n\n"
            f"{scope_section}"
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

        valid_ids = {f["id"] for f in self.fact_store["facts"]}
        already_set = set(already_earned)
        result["newly_earned"] = [
            fid for fid in result.get("newly_earned", [])
            if fid in valid_ids and fid not in already_set
        ]

        result = self._apply_broad_scope_override(question, already_earned, result)
        result = self._apply_conversational_ack_override(question, result)

        result["newly_earned"] = [
            fid for fid in result.get("newly_earned", [])
            if fid in valid_ids and fid not in already_set
        ]
        return result

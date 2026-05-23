"""
Orchestrates a single conversational turn.

Flow per turn:
  1. Intent Agent classifies the student's question and returns the IDs of
     facts they've just earned.
  2. The orchestrator hydrates those IDs (plus previously earned IDs) to full
     fact objects.
  3. Patient Agent generates the spoken response using ONLY those facts.
  4. The earned-fact set is updated and returned for the next turn.
"""

from intent_agent import IntentAgent
from patient_agent import PatientAgent


class OsceTurn:
    def __init__(
        self,
        intent_agent: IntentAgent,
        patient_agent: PatientAgent,
        fact_store: dict,
    ):
        self.intent_agent = intent_agent
        self.patient_agent = patient_agent
        self.fact_lookup = {f["id"]: f for f in fact_store["facts"]}

    def process(
        self,
        student_question: str,
        history: list[dict],
        already_earned: list[str],
    ) -> dict:
        # Step 1: intent + disclosure decision
        intent_result = self.intent_agent.classify(
            question=student_question,
            history=history,
            already_earned=already_earned,
        )

        newly_earned_ids: list[str] = intent_result.get("newly_earned", [])
        utterance_type: str = intent_result.get("utterance_type", "")
        rationale: str = intent_result.get("rationale", "")

        # Step 2: hydrate facts. Include both newly earned AND previously earned
        # so the patient can naturally reference earlier-disclosed information.
        all_available_ids = list({*newly_earned_ids, *already_earned})
        available_facts = [
            self.fact_lookup[fid]
            for fid in all_available_ids
            if fid in self.fact_lookup
        ]

        # Step 3: generate patient response
        patient_response = self.patient_agent.respond(
            question=student_question,
            available_facts=available_facts,
            history=history,
            utterance_type=utterance_type,
        )

        # Step 4: bookkeeping
        updated_earned = list({*already_earned, *newly_earned_ids})

        return {
            "patient_response": patient_response,
            "newly_earned": newly_earned_ids,
            "all_earned": updated_earned,
            "utterance_type": utterance_type,
            "rationale": rationale,
        }

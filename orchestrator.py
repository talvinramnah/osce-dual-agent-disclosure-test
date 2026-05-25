"""
Orchestrates a single conversational turn.

Flow per turn:
  1. Intent Agent classifies the student's question and returns the IDs of
     facts they've just earned.
  2. The orchestrator hydrates those IDs (plus previously earned IDs) to full
     fact objects.
  3. Patient Agent generates the spoken response using ONLY those facts.
     SHORT-CIRCUIT: if the gate classifies as `filler_only`, the patient
     agent is NOT called. The patient stays silent (empty response) and the
     UI renders a faint placeholder. This is more natural than emitting a
     robotic "Mm" on every pure-acknowledgement turn.
  4. The earned-fact set is updated and returned for the next turn.
"""

from intent_agent import IntentAgent
from patient_agent import PatientAgent

# Utterance types that should produce no patient speech at all. The orchestrator
# short-circuits before the patient agent is called; the UI renders a faint
# silent-acknowledgement indicator in place of a chat bubble.
SILENT_UTTERANCE_TYPES = {"filler_only"}


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

        # Step 2: hydrate ONLY the newly authorised facts to full objects (with
        # canonical wording). Previously disclosed facts are passed as IDs only -
        # the patient can see them in the conversation history and reference them
        # if asked, but they should NOT be restated as if disclosing for the first
        # time. This split prevents the "recap-dumping" failure mode where every
        # turn with newly_earned=[] would otherwise re-surface the entire history.
        newly_authorised_facts = [
            self.fact_lookup[fid]
            for fid in newly_earned_ids
            if fid in self.fact_lookup
        ]
        previously_disclosed_ids = [
            fid for fid in already_earned if fid in self.fact_lookup
        ]

        # Step 3: generate patient response (or skip entirely for silent types).
        if utterance_type in SILENT_UTTERANCE_TYPES:
            patient_response = ""
        else:
            patient_response = self.patient_agent.respond(
                question=student_question,
                newly_authorised_facts=newly_authorised_facts,
                previously_disclosed_ids=previously_disclosed_ids,
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

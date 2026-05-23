"""
Bleep64 OSCE Prototype - Streamlit app.

Test harness for the two-agent OSCE patient architecture. Pick a patient
from the sidebar, then talk (or type) to them. The right-hand panel shows
the disclosure debug for the most recent turn.
"""

import json
import os
import tempfile
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

from intent_agent import IntentAgent
from orchestrator import OsceTurn
from patient_agent import PatientAgent
from patients.registry import PATIENTS, get_patient, list_patients, validate
from stt import STT
from tts import TTS

# Local dev: load from .env. Cloud: secrets come from st.secrets.
load_dotenv()

# Bridge st.secrets -> os.environ so downstream modules (tts.py, stt.py,
# openai client, etc.) that read os.environ["..."] keep working unchanged.
for _key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
    if _key not in os.environ and _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

VOICE_ID = "tkWr6klIDADZ7T8TWUuj"
INTENT_MODEL = "gpt-4o-mini"
PATIENT_MODEL = "gpt-4o"

DEFAULT_PATIENT_ID = next(iter(PATIENTS))


# ---- Cached resources, keyed by patient_id ----

@st.cache_resource
def load_fact_store(patient_id: str) -> dict:
    path: Path = get_patient(patient_id)["facts_path"]
    with open(path) as f:
        return json.load(f)


@st.cache_resource
def load_prompts(patient_id: str) -> dict:
    entry = get_patient(patient_id)
    return {
        "intent": entry["intent_prompt_path"].read_text(),
        "patient": entry["patient_prompt_path"].read_text(),
        "few_shots": yaml.safe_load(entry["few_shots_path"].read_text()),
    }


@st.cache_resource
def build_turn(patient_id: str) -> OsceTurn:
    fact_store = load_fact_store(patient_id)
    prompts = load_prompts(patient_id)
    intent = IntentAgent(
        model=INTENT_MODEL,
        system_prompt=prompts["intent"],
        few_shots=prompts["few_shots"],
        fact_store=fact_store,
    )
    patient = PatientAgent(
        model=PATIENT_MODEL,
        system_prompt=prompts["patient"],
    )
    return OsceTurn(intent, patient, fact_store)


@st.cache_resource
def build_tts() -> TTS:
    return TTS(voice_id=VOICE_ID)


@st.cache_resource
def build_stt() -> STT:
    return STT()


# ---- Session state ----

def init_state():
    if "patient_id" not in st.session_state:
        st.session_state.patient_id = DEFAULT_PATIENT_ID
    if "history" not in st.session_state:
        st.session_state.history = []
    if "earned" not in st.session_state:
        st.session_state.earned = []
    if "last_meta" not in st.session_state:
        st.session_state.last_meta = None


def reset_conversation():
    st.session_state.history = []
    st.session_state.earned = []
    st.session_state.last_meta = None


# ---- UI ----

st.set_page_config(
    page_title="Bleep64 OSCE Prototype",
    page_icon="🩺",
    layout="wide",
)
init_state()

# Fail loud if the registry points at missing files - much nicer than a
# FileNotFoundError appearing several layers deep on the first turn.
_problems = validate()
if _problems:
    st.error("Patient registry is misconfigured:\n\n" + "\n".join(f"- {p}" for p in _problems))
    st.stop()

# ---- Sidebar: patient selector ----

with st.sidebar:
    st.header("Patient")
    patient_entries = list_patients()
    patient_ids = [p["id"] for p in patient_entries]
    current_idx = patient_ids.index(st.session_state.patient_id)
    selected_id = st.radio(
        "Choose a case",
        options=patient_ids,
        index=current_idx,
        format_func=lambda pid: get_patient(pid)["display_name"],
        label_visibility="collapsed",
        key="patient_id_selector",
    )
    selected_entry = get_patient(selected_id)
    st.caption(selected_entry["subtitle"])

    if selected_id != st.session_state.patient_id:
        st.session_state.patient_id = selected_id
        reset_conversation()
        st.rerun()

    st.markdown("---")
    if st.button("Reset conversation", type="secondary", use_container_width=True):
        reset_conversation()
        st.rerun()

# ---- Main pane ----

current_entry = get_patient(st.session_state.patient_id)
patient_name = current_entry["display_name"]

st.title(f"Bleep64 OSCE Prototype - {patient_name}")
st.caption(
    "Two-agent disclosure-gated patient simulator. "
    "Type or record a question. The right-hand panel shows the disclosure gate's reasoning."
)

left, right = st.columns([3, 2])

with left:
    st.subheader("Conversation")
    chat_container = st.container(height=500)
    with chat_container:
        if not st.session_state.history:
            st.write(
                "_(Start by asking an opening question, e.g. 'What brings you in today?')_"
            )
        for turn in st.session_state.history:
            with st.chat_message("user"):
                st.write(turn["student"])
            with st.chat_message("assistant"):
                st.write(turn["patient"])
                if turn.get("audio"):
                    st.audio(turn["audio"], format="audio/mp3", autoplay=False)

    st.markdown("---")
    input_mode = st.radio(
        "Input mode",
        ["Text", "Voice"],
        horizontal=True,
        label_visibility="collapsed",
    )

    submitted_text: str | None = None

    if input_mode == "Text":
        with st.form("text_input_form", clear_on_submit=True):
            text = st.text_input(f"Your question to {patient_name}:")
            submit = st.form_submit_button("Send")
            if submit and text.strip():
                submitted_text = text.strip()
    else:
        audio = st.audio_input("Record your question")
        if audio is not None:
            if st.button("Transcribe and send"):
                with st.spinner("Transcribing..."):
                    with tempfile.NamedTemporaryFile(
                        suffix=".wav", delete=False
                    ) as tmp:
                        tmp.write(audio.getvalue())
                        tmp_path = tmp.name
                    try:
                        submitted_text = build_stt().transcribe(tmp_path)
                    finally:
                        os.unlink(tmp_path)
                if submitted_text:
                    st.info(f"Heard: {submitted_text}")

    if submitted_text:
        turn_engine = build_turn(st.session_state.patient_id)
        with st.spinner("Patient thinking..."):
            try:
                result = turn_engine.process(
                    student_question=submitted_text,
                    history=st.session_state.history,
                    already_earned=st.session_state.earned,
                )
            except Exception as e:
                st.error(f"Turn failed: {e}")
                st.stop()

        audio_bytes: bytes | None = None
        with st.spinner("Generating voice..."):
            try:
                audio_bytes = build_tts().synthesize(result["patient_response"])
            except Exception as e:
                st.warning(f"TTS failed (continuing without audio): {e}")

        st.session_state.history.append({
            "student": submitted_text,
            "patient": result["patient_response"],
            "audio": audio_bytes,
        })
        st.session_state.earned = result["all_earned"]
        st.session_state.last_meta = {
            "newly_earned": result["newly_earned"],
            "utterance_type": result["utterance_type"],
            "rationale": result["rationale"],
        }
        st.rerun()

with right:
    st.subheader("Disclosure debug")
    st.caption("What the intent agent decided this turn.")

    if st.session_state.last_meta:
        meta = st.session_state.last_meta
        st.metric("Utterance type", meta["utterance_type"] or "(none)")
        st.write("**Newly earned this turn:**")
        if meta["newly_earned"]:
            for fid in meta["newly_earned"]:
                st.write(f"- `{fid}`")
        else:
            st.write("_(nothing new)_")
        st.write("**Rationale:**")
        st.info(meta["rationale"] or "(none)")
    else:
        st.write("_(no turn yet)_")

    st.markdown("---")
    st.write(f"**All facts earned so far ({len(st.session_state.earned)}):**")
    if st.session_state.earned:
        for fid in st.session_state.earned:
            st.write(f"- `{fid}`")
    else:
        st.write("_(none)_")

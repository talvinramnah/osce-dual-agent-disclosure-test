"""
Bleep64 OSCE Prototype - Streamlit app.

Test harness for the two-agent OSCE patient architecture. Pick a patient
from the sidebar, then talk (or type) to them. The right-hand panel shows
the disclosure debug for the most recent turn.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
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

MAX_ARCHIVED_SESSIONS = 10


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first_name(display_name: str) -> str:
    """First token of a display name, e.g. 'Michael' from 'Michael Doyle'."""
    return display_name.split()[0] if display_name else "Patient"


def _silent_ack_text(display_name: str) -> str:
    """Italic placeholder shown when the patient stayed silent on a filler turn."""
    return f"_({_first_name(display_name)} acknowledges silently)_"


def format_session_as_markdown(
    *,
    patient_display_name: str,
    started_at: str | None,
    ended_at: str | None,
    turns: list[dict],
    earned: list[str],
) -> str:
    """Render a chat session as Markdown. Shared by live and archived exports."""
    lines: list[str] = [f"# OSCE Conversation \u2014 {patient_display_name}", ""]
    if started_at:
        lines.append(f"- **Started:** {started_at}")
    if ended_at:
        lines.append(f"- **Ended:** {ended_at}")
    lines.append(f"- **Turns:** {len(turns)}")
    lines.append(f"- **Facts disclosed:** {len(earned)}")
    lines.append("")

    if turns:
        for turn in turns:
            patient_line = (turn.get("patient") or "").strip()
            if not patient_line:
                # Silent acknowledgement turn (filler_only); omit from transcript.
                continue
            lines.append("---")
            lines.append("")
            lines.append(f"**Student:** {turn.get('student', '').strip()}")
            lines.append("")
            lines.append(f"**Patient:** {patient_line}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Facts disclosed")
    lines.append("")
    if earned:
        for fid in earned:
            lines.append(f"- `{fid}`")
    else:
        lines.append("_(none)_")
    lines.append("")
    return "\n".join(lines)


def _filename_stamp(iso_ts: str | None) -> str:
    """Compact YYYYMMDD-HHMMSS for filenames. Falls back to now if missing."""
    try:
        ts = datetime.fromisoformat(iso_ts) if iso_ts else datetime.now(timezone.utc)
    except ValueError:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y%m%d-%H%M%S")


def _format_local_time(iso_ts: str | None, fmt: str = "%H:%M") -> str:
    """Render a UTC ISO timestamp in the viewer's local time. '\u2014' on bad input."""
    if not iso_ts:
        return "\u2014"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    return dt.astimezone().strftime(fmt)


def _session_label(session: dict) -> str:
    return (
        f"{session['patient_display_name']} \u00b7 "
        f"{len(session['turns'])} turns \u00b7 "
        f"{_format_local_time(session['ended_at'])}"
    )


def _find_session(session_id: int | None) -> dict | None:
    if session_id is None:
        return None
    for s in st.session_state.sessions:
        if s["id"] == session_id:
            return s
    return None


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
    if "sessions" not in st.session_state:
        st.session_state.sessions = []
    if "session_counter" not in st.session_state:
        st.session_state.session_counter = 0
    if "session_started_at" not in st.session_state:
        st.session_state.session_started_at = None
    if "viewing_session_id" not in st.session_state:
        st.session_state.viewing_session_id = None


def archive_current_session():
    """Snapshot the live chat into st.session_state.sessions. No-op if empty.

    Strips audio bytes to keep the archive small. FIFO-evicts the oldest entry
    when the cap is exceeded.
    """
    if not st.session_state.history:
        return

    pid = st.session_state.patient_id
    entry = {
        "id": st.session_state.session_counter,
        "patient_id": pid,
        "patient_display_name": get_patient(pid)["display_name"],
        "started_at": st.session_state.session_started_at or _utcnow_iso(),
        "ended_at": _utcnow_iso(),
        "turns": [
            {"student": t["student"], "patient": t["patient"]}
            for t in st.session_state.history
        ],
        "earned": list(st.session_state.earned),
    }
    st.session_state.sessions.append(entry)
    st.session_state.session_counter += 1

    if len(st.session_state.sessions) > MAX_ARCHIVED_SESSIONS:
        st.session_state.sessions = st.session_state.sessions[-MAX_ARCHIVED_SESSIONS:]


def reset_conversation():
    archive_current_session()
    st.session_state.history = []
    st.session_state.earned = []
    st.session_state.last_meta = None
    st.session_state.session_started_at = None


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
        # Archive against the OLD patient before flipping the id; otherwise the
        # archived entry would be labelled with the new patient's name.
        archive_current_session()
        st.session_state.patient_id = selected_id
        st.session_state.history = []
        st.session_state.earned = []
        st.session_state.last_meta = None
        st.session_state.session_started_at = None
        st.session_state.viewing_session_id = None
        st.rerun()

    st.markdown("---")
    has_live_chat = bool(st.session_state.history)
    if st.button(
        "End & save session",
        type="primary",
        use_container_width=True,
        disabled=not has_live_chat,
        help="Save the current chat to your in-memory history (last 10).",
    ):
        reset_conversation()
        st.session_state.viewing_session_id = None
        st.rerun()
    if st.button("Reset conversation", type="secondary", use_container_width=True):
        reset_conversation()
        st.session_state.viewing_session_id = None
        st.rerun()

    if has_live_chat:
        live_md = format_session_as_markdown(
            patient_display_name=get_patient(st.session_state.patient_id)["display_name"],
            started_at=st.session_state.session_started_at,
            ended_at=None,
            turns=st.session_state.history,
            earned=st.session_state.earned,
        )
        st.download_button(
            label="Download chat (.md)",
            data=live_md,
            file_name=f"osce-{st.session_state.patient_id}-{_filename_stamp(None)}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    # ---- Sidebar: past sessions list ----
    st.markdown("---")
    n_sessions = len(st.session_state.sessions)
    st.subheader(f"Past sessions ({n_sessions}/{MAX_ARCHIVED_SESSIONS})")
    if n_sessions == 0:
        st.caption("_No past sessions yet. Save one with 'End & save session'._")
    else:
        for s in reversed(st.session_state.sessions):
            is_viewing = st.session_state.viewing_session_id == s["id"]
            if st.button(
                _session_label(s),
                key=f"past_session_{s['id']}",
                use_container_width=True,
                disabled=is_viewing,
                help="Open this past conversation (read-only)."
                if not is_viewing
                else "Currently viewing this session.",
            ):
                st.session_state.viewing_session_id = s["id"]
                st.rerun()

# ---- Main pane ----

current_entry = get_patient(st.session_state.patient_id)
patient_name = current_entry["display_name"]

viewing_session = _find_session(st.session_state.viewing_session_id)
# Self-heal: if the viewing id no longer exists (e.g. evicted by FIFO), drop it.
if st.session_state.viewing_session_id is not None and viewing_session is None:
    st.session_state.viewing_session_id = None

if viewing_session is not None:
    st.title(f"Bleep64 OSCE Prototype - Past session")
    st.caption(
        "Viewing an archived conversation (read-only). "
        "Use '\u2190 Back to live' to return to your current chat."
    )

    left, right = st.columns([3, 2])

    with left:
        banner_cols = st.columns([3, 1, 1])
        with banner_cols[0]:
            st.markdown(
                f"**{viewing_session['patient_display_name']}**  \n"
                f"Started {_format_local_time(viewing_session['started_at'], '%Y-%m-%d %H:%M')} "
                f"\u00b7 Ended {_format_local_time(viewing_session['ended_at'], '%H:%M')} "
                f"\u00b7 {len(viewing_session['turns'])} turns "
                f"\u00b7 {len(viewing_session['earned'])} facts"
            )
        with banner_cols[1]:
            if st.button("\u2190 Back to live", use_container_width=True, key="back_to_live"):
                st.session_state.viewing_session_id = None
                st.rerun()
        with banner_cols[2]:
            archived_md = format_session_as_markdown(
                patient_display_name=viewing_session["patient_display_name"],
                started_at=viewing_session["started_at"],
                ended_at=viewing_session["ended_at"],
                turns=viewing_session["turns"],
                earned=viewing_session["earned"],
            )
            st.download_button(
                label="Download (.md)",
                data=archived_md,
                file_name=(
                    f"osce-{viewing_session['patient_id']}-"
                    f"{_filename_stamp(viewing_session['ended_at'])}.md"
                ),
                mime="text/markdown",
                use_container_width=True,
                key=f"download_archived_{viewing_session['id']}",
            )

        st.subheader("Conversation")
        chat_container = st.container(height=500)
        with chat_container:
            if not viewing_session["turns"]:
                st.write("_(empty)_")
            for turn in viewing_session["turns"]:
                with st.chat_message("user"):
                    st.write(turn["student"])
                if turn.get("patient"):
                    with st.chat_message("assistant"):
                        st.write(turn["patient"])
                else:
                    st.markdown(_silent_ack_text(viewing_session["patient_display_name"]))

    with right:
        st.subheader("Disclosure debug")
        st.info(
            "Disclosure debug is only available for live conversations. "
            "Per-turn intent metadata isn't archived."
        )
        st.markdown("---")
        st.write(f"**Facts disclosed in this session ({len(viewing_session['earned'])}):**")
        if viewing_session["earned"]:
            for fid in viewing_session["earned"]:
                st.write(f"- `{fid}`")
        else:
            st.write("_(none)_")

    st.stop()


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
            if turn.get("patient"):
                with st.chat_message("assistant"):
                    st.write(turn["patient"])
                    if turn.get("audio"):
                        st.audio(turn["audio"], format="audio/mp3", autoplay=False)
            else:
                st.markdown(_silent_ack_text(patient_name))

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
        if st.session_state.session_started_at is None:
            st.session_state.session_started_at = _utcnow_iso()
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
        if result["patient_response"]:
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
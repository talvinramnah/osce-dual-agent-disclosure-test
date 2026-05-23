"""
Registry of OSCE patient cases.

Add a new patient by creating a folder under `patients/` containing the four
required files (`facts.json`, `intent_system.md`, `patient_system.md`,
`disclosure_few_shots.yaml`) and registering it here. `app.py` reads from
this registry to populate the sidebar selector and load case files.
"""

from pathlib import Path

PATIENTS_DIR = Path(__file__).parent

REQUIRED_FILES = (
    "facts.json",
    "intent_system.md",
    "patient_system.md",
    "disclosure_few_shots.yaml",
)


def _paths(folder: str) -> dict:
    base = PATIENTS_DIR / folder
    return {
        "facts_path": base / "facts.json",
        "intent_prompt_path": base / "intent_system.md",
        "patient_prompt_path": base / "patient_system.md",
        "few_shots_path": base / "disclosure_few_shots.yaml",
    }


PATIENTS: dict[str, dict] = {
    "michael_doyle": {
        "id": "michael_doyle",
        "display_name": "Michael Doyle",
        "subtitle": "43yo M, A&E, right-sided flank pain",
        **_paths("michael_doyle"),
    },
    "daniel_oconnor": {
        "id": "daniel_oconnor",
        "display_name": "Daniel O'Connor",
        "subtitle": "20yo M, A&E, central chest pain",
        **_paths("daniel_oconnor"),
    },
}


def get_patient(patient_id: str) -> dict:
    if patient_id not in PATIENTS:
        raise KeyError(
            f"Unknown patient_id {patient_id!r}. "
            f"Known: {sorted(PATIENTS)}"
        )
    return PATIENTS[patient_id]


def list_patients() -> list[dict]:
    """Ordered list of patient entries, in insertion order of PATIENTS."""
    return list(PATIENTS.values())


def validate() -> list[str]:
    """Return a list of human-readable problems with the registry, or [] if OK.

    Used by app.py at startup so misconfiguration shows up as a clear error
    instead of a confusing FileNotFoundError mid-turn.
    """
    problems: list[str] = []
    for pid, entry in PATIENTS.items():
        for key in ("facts_path", "intent_prompt_path", "patient_prompt_path", "few_shots_path"):
            path = entry[key]
            if not path.exists():
                problems.append(f"{pid}: missing {key} -> {path}")
    return problems

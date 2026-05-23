"""OpenAI Whisper wrapper for speech-to-text."""

import os
from openai import OpenAI


class STT:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "whisper-1",
    ):
        self.client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self.model = model

    def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            result = self.client.audio.transcriptions.create(
                model=self.model,
                file=f,
            )
        return (result.text or "").strip()

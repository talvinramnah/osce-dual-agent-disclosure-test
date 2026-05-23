"""ElevenLabs TTS wrapper using Michael Doyle's configured voice."""

import os
from elevenlabs.client import ElevenLabs


class TTS:
    def __init__(
        self,
        voice_id: str,
        api_key: str | None = None,
        model_id: str = "eleven_turbo_v2_5",
    ):
        self.client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])
        self.voice_id = voice_id
        self.model_id = model_id

    def synthesize(self, text: str) -> bytes:
        """Generate speech audio for the given text. Returns mp3 bytes."""
        if not text or not text.strip():
            return b""
        audio_iter = self.client.text_to_speech.convert(
            voice_id=self.voice_id,
            text=text,
            model_id=self.model_id,
            output_format="mp3_44100_128",
        )
        return b"".join(audio_iter)

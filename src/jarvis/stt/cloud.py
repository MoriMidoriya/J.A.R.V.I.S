"""Cloud STT via OpenAI (gpt-4o-mini-transcribe) or Groq (whisper-large-v3-turbo) — needs the matching API key."""

from __future__ import annotations

import io
import os
import time

import httpx
import numpy as np
import soundfile as sf

from jarvis.stt.base import STTEngine, Transcript, sanitize

ENDPOINTS = {
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "OPENAI_API_KEY", "gpt-4o-mini-transcribe"),
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "GROQ_API_KEY", "whisper-large-v3-turbo"),
}


class CloudSTT(STTEngine):
    def __init__(self, provider: str = "groq", language: str | None = "en") -> None:
        self.url, self.key_env, self.model = ENDPOINTS[provider]
        self.language = language

    def transcribe(self, audio_int16_16k: np.ndarray) -> Transcript:
        key = os.environ.get(self.key_env)
        if not key:
            raise RuntimeError(f"{self.key_env} not set")
        t0 = time.perf_counter()
        buf = io.BytesIO()
        sf.write(buf, audio_int16_16k, 16000, format="WAV", subtype="PCM_16")
        buf.seek(0)
        data = {"model": self.model}
        if self.language:
            data["language"] = self.language
        r = httpx.post(self.url, headers={"Authorization": f"Bearer {key}"}, data=data, files={"file": ("audio.wav", buf, "audio/wav")}, timeout=30)
        r.raise_for_status()
        text = sanitize(r.json().get("text", ""))
        return Transcript(text=text, language=self.language, elapsed_ms=int((time.perf_counter() - t0) * 1000))

    def warmup(self) -> None:
        pass

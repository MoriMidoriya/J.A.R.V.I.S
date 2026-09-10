"""OpenAI TTS (needs OPENAI_API_KEY). Voices: alloy, echo, fable, onyx, nova, shimmer, ash, ballad, coral, sage, verse."""

from __future__ import annotations

import io
import os
from collections.abc import Iterator

import httpx
import numpy as np
import soundfile as sf

from jarvis.tts.base import TTSEngine
from jarvis.tts.kokoro import clean_for_speech


class OpenAITTS(TTSEngine):
    sample_rate = 24000

    def __init__(self, voice: str = "onyx", speed: float = 1.0, model: str = "gpt-4o-mini-tts") -> None:
        self.voice = voice if voice and not voice.startswith(("bm_", "bf_", "am_", "af_")) else "onyx"
        self.speed = speed
        self.model = model

    def synthesize(self, text: str) -> Iterator[np.ndarray]:
        text = clean_for_speech(text)
        if not text:
            return
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        r = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.model, "input": text, "voice": self.voice, "speed": self.speed, "response_format": "wav", "instructions": "Calm, precise British butler. Dry wit."},
            timeout=60,
        )
        r.raise_for_status()
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != self.sample_rate:
            import math

            idx = np.linspace(0, len(data) - 1, math.ceil(len(data) * self.sample_rate / sr))
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        yield data

"""ElevenLabs TTS (needs ELEVENLABS_API_KEY). `voice` is a voice_id or a name from your voice library."""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import numpy as np

from jarvis.tts.base import TTSEngine
from jarvis.tts.kokoro import clean_for_speech


class ElevenLabsTTS(TTSEngine):
    sample_rate = 24000

    def __init__(self, voice: str, model_id: str = "eleven_flash_v2_5") -> None:
        self.voice = voice
        self.model_id = model_id
        self._voice_id: str | None = None

    def _resolve_voice(self, key: str) -> str:
        if self._voice_id:
            return self._voice_id
        if len(self.voice) >= 20 and " " not in self.voice:
            self._voice_id = self.voice
            return self._voice_id
        r = httpx.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=20)
        r.raise_for_status()
        for v in r.json().get("voices", []):
            if v["name"].lower() == self.voice.lower():
                self._voice_id = v["voice_id"]
                return self._voice_id
        raise RuntimeError(f"ElevenLabs voice '{self.voice}' not found")

    def synthesize(self, text: str) -> Iterator[np.ndarray]:
        text = clean_for_speech(text)
        if not text:
            return
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        vid = self._resolve_voice(key)
        with httpx.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream",
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": key},
            json={"text": text, "model_id": self.model_id},
            timeout=60,
        ) as r:
            r.raise_for_status()
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                usable = len(buf) - (len(buf) % 2)
                if usable >= 4800:  # ~100 ms
                    yield np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32) / 32768.0
                    buf = buf[usable:]
            if len(buf) >= 2:
                usable = len(buf) - (len(buf) % 2)
                yield np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32) / 32768.0

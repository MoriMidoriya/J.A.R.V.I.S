"""STT interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

# Whisper is notorious for inventing these on silence / breath noise.
HALLUCINATIONS = {
    "thank you.", "thanks.", "thank you", "you", "you.", "bye.", "bye bye.", "okay.", "ok.", "so.", "hmm.", "mm-hmm.",
    "thank you for watching.", "thanks for watching.", "thank you for watching!", "subtitles by the amara.org community",
    "please subscribe.", "i'm sorry.", "the end.", ".", "…", "uh", "um",
}


@dataclass
class Transcript:
    text: str
    language: str | None = None
    confidence: float | None = None  # 0..1 where available
    elapsed_ms: int = 0

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def sanitize(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if t.lower() in HALLUCINATIONS:
        return ""
    # Drop a leading wake word echo: "Jarvis, daddy's home" -> "daddy's home"
    t = re.sub(r"^(?:hey|hi|ok|okay|yo)?[\s,]*jarvis[\s,.!?]*", "", t, flags=re.IGNORECASE)
    return t.strip()


class STTEngine(ABC):
    @abstractmethod
    def transcribe(self, audio_int16_16k: np.ndarray) -> Transcript: ...

    def warmup(self) -> None:
        self.transcribe(np.zeros(16000, dtype=np.int16))

"""Silero VAD end-pointing: feed frames, get told when an utterance starts and when it has ended."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from loguru import logger

WINDOW = 512  # Silero operates on 512-sample windows at 16 kHz (32 ms)


@dataclass
class Utterance:
    audio: np.ndarray  # int16 16 kHz
    duration_s: float


@dataclass
class VADState:
    speaking: bool = False
    silence_ms: float = 0.0
    speech_ms: float = 0.0
    buf: list[np.ndarray] = field(default_factory=list)


class SileroVAD:
    def __init__(self, threshold: float = 0.5, silence_ms: int = 600, min_speech_ms: int = 250, max_utterance_s: float = 15) -> None:
        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        self._model = load_silero_vad(onnx=True)
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_s = max_utterance_s
        self._pending = np.zeros(0, dtype=np.int16)
        self.state = VADState()
        logger.info("Silero VAD loaded (threshold {}, endpoint {} ms)", threshold, silence_ms)

    def reset(self, preroll: np.ndarray | None = None) -> None:
        self._model.reset_states()
        self._pending = np.zeros(0, dtype=np.int16)
        self.state = VADState()
        if preroll is not None and preroll.size:
            self.state.buf.append(preroll)

    def prob(self, window: np.ndarray) -> float:
        import torch

        x = torch.from_numpy(window.astype(np.float32) / 32768.0)
        return float(self._model(x, 16000).item())

    def feed(self, frame: np.ndarray) -> Utterance | None:
        """Push one int16 frame. Returns a completed Utterance when end-of-speech is detected."""
        st = self.state
        st.buf.append(frame)
        self._pending = np.concatenate([self._pending, frame])
        frame_ms = len(frame) * 1000 / 16000

        probs = []
        while len(self._pending) >= WINDOW:
            probs.append(self.prob(self._pending[:WINDOW]))
            self._pending = self._pending[WINDOW:]
        if not probs:
            return None
        is_speech = max(probs) >= self.threshold

        if is_speech:
            if not st.speaking:
                st.speaking = True
                logger.debug("speech start")
            st.speech_ms += frame_ms
            st.silence_ms = 0.0
        elif st.speaking:
            st.silence_ms += frame_ms

        total_s = sum(len(b) for b in st.buf) / 16000
        ended = st.speaking and st.silence_ms >= self.silence_ms
        too_long = st.speaking and total_s >= self.max_utterance_s
        if ended or too_long:
            audio = np.concatenate(st.buf)
            spoke_enough = st.speech_ms >= self.min_speech_ms
            self.reset()
            if not spoke_enough:
                logger.debug("discarding blip ({} ms speech)", st.speech_ms)
                return None
            return Utterance(audio=audio, duration_s=len(audio) / 16000)
        return None

    @property
    def speaking(self) -> bool:
        return self.state.speaking

    def elapsed_s(self) -> float:
        return sum(len(b) for b in self.state.buf) / 16000

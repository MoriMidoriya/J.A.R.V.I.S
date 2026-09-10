"""openWakeWord wrapper — "hey jarvis" detection on 80 ms / 16 kHz int16 frames (ONNX, CPU, ~1 ms/frame)."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import numpy as np
from loguru import logger

from jarvis.config import MODELS_DIR

OWW_DIR = MODELS_DIR / "openwakeword"
RELEASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
FEATURE_FILES = ("melspectrogram.onnx", "embedding_model.onnx")
BUILTIN = {"hey_jarvis": "hey_jarvis_v0.1.onnx", "alexa": "alexa_v0.1.onnx", "hey_mycroft": "hey_mycroft_v0.1.onnx"}


def _fetch(name: str) -> Path:
    OWW_DIR.mkdir(parents=True, exist_ok=True)
    dst = OWW_DIR / name
    if not dst.exists():
        logger.info("downloading wake word model {}", name)
        r = httpx.get(RELEASE + name, follow_redirects=True, timeout=120)
        r.raise_for_status()
        dst.write_bytes(r.content)
    return dst


def resolve_model(spec: str) -> Path:
    """`hey_jarvis` (built-in) or a path to a custom .onnx."""
    p = Path(spec)
    if p.suffix == ".onnx" and p.exists():
        return p
    if spec in BUILTIN:
        return _fetch(BUILTIN[spec])
    raise FileNotFoundError(f"unknown wake word model {spec!r}; use one of {list(BUILTIN)} or a path to an .onnx file")


class WakeWord:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5, cooldown_s: float = 2.0, noise_suppression: bool = False) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._last_hit = 0.0
        self.model_path = resolve_model(model)
        for f in FEATURE_FILES:
            _fetch(f)
        from openwakeword.model import Model

        self._model = Model(
            wakeword_models=[str(self.model_path)],
            inference_framework="onnx",
            melspec_model_path=str(OWW_DIR / FEATURE_FILES[0]),
            embedding_model_path=str(OWW_DIR / FEATURE_FILES[1]),
            enable_speex_noise_suppression=noise_suppression,
        )
        self.name = next(iter(self._model.models.keys()))
        logger.info("wake word '{}' loaded (threshold {})", self.name, threshold)

    def score(self, frame: np.ndarray) -> float:
        """Feed one int16 frame (ideally 1280 samples); returns the current detection score 0..1."""
        preds = self._model.predict(frame)
        return float(preds.get(self.name, 0.0))

    def detect(self, frame: np.ndarray) -> float | None:
        """Returns the score when a detection fires (respecting cooldown), else None."""
        s = self.score(frame)
        now = time.monotonic()
        if s >= self.threshold and now - self._last_hit > self.cooldown_s:
            self._last_hit = now
            self.reset()
            return s
        return None

    def reset(self) -> None:
        self._model.reset()

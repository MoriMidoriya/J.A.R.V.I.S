"""TTS interface: synthesize(text) yields float32 mono numpy chunks at `sample_rate`."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

import numpy as np
from loguru import logger


class TTSEngine(ABC):
    sample_rate: int = 24000

    @abstractmethod
    def synthesize(self, text: str) -> Iterator[np.ndarray]: ...

    def warmup(self) -> None:
        """Load weights / run a tiny synthesis so the first real utterance is fast."""
        t0 = time.perf_counter()
        for _ in self.synthesize("Systems online."):
            pass
        logger.debug("tts warmup synthesis (incl. model load) took {:.1f}s", time.perf_counter() - t0)

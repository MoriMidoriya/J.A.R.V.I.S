"""Kokoro-82M local TTS (Apache 2.0). British English pipeline, GPU if available."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import numpy as np
from loguru import logger

from jarvis.tts.base import TTSEngine

# Small pronunciation fixes for spoken output.
REPLACEMENTS = [
    (re.compile(r"\bJ\.A\.R\.V\.I\.S\.?", re.I), "Jarvis"),
    (re.compile(r"\bPC\b"), "P C"),
    (re.compile(r"\bGPU\b"), "G P U"),
    (re.compile(r"\bCPU\b"), "C P U"),
    (re.compile(r"\bRAM\b"), "ram"),
    (re.compile(r"\bVRAM\b"), "V ram"),
    (re.compile(r"\bUI\b"), "U I"),
    (re.compile(r"\bAPI\b"), "A P I"),
    (re.compile(r"\bURL\b"), "U R L"),
    (re.compile(r"\bWi-?Fi\b", re.I), "wifi"),
    (re.compile(r"(\d+)%"), r"\1 percent"),
    (re.compile(r"[*_`#>]+"), ""),  # stray markdown
]


def clean_for_speech(text: str) -> str:
    for rx, rep in REPLACEMENTS:
        text = rx.sub(rep, text)
    # Windows paths are unpronounceable; say just the file name.
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)*([^\\\s\"']+)", r"\1", text)
    text = re.sub(r"https?://\S+", "the link", text)
    return re.sub(r"\s+", " ", text).strip()


class KokoroTTS(TTSEngine):
    sample_rate = 24000

    def __init__(self, voice: str = "bm_george", speed: float = 1.0, lang_code: str | None = None) -> None:
        self.voice = voice
        self.speed = speed
        # 'a' American, 'b' British — infer from the voice prefix (bm_/bf_ are British).
        self.lang_code = lang_code or ("b" if voice.startswith("b") else "a")
        self._pipe = None

    def _pipeline(self):  # noqa: ANN202
        if self._pipe is None:
            t0 = time.perf_counter()
            import torch

            from jarvis.logging_setup import reapply, sinks_frozen

            with sinks_frozen():  # kokoro calls logger.remove()/add() at import; keep our console+file sinks intact
                from kokoro import KPipeline
            reapply()  # belt and braces
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._pipe = KPipeline(lang_code=self.lang_code, repo_id="hexgrad/Kokoro-82M", device=device)
            logger.info("Kokoro loaded on {} in {:.1f}s (voice {})", device, time.perf_counter() - t0, self.voice)
        return self._pipe

    def synthesize(self, text: str) -> Iterator[np.ndarray]:
        text = clean_for_speech(text)
        if not text:
            return
        pipe = self._pipeline()
        for result in pipe(text, voice=self.voice, speed=self.speed, split_pattern=r"\n+"):
            audio = result.audio
            if audio is None:
                continue
            arr = audio.detach().cpu().numpy().astype(np.float32) if hasattr(audio, "detach") else np.asarray(audio, dtype=np.float32)
            yield arr

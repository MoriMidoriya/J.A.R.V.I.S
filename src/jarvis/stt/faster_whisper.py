"""faster-whisper (CTranslate2) STT on CUDA."""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
from loguru import logger

from jarvis.stt.base import STTEngine, Transcript, sanitize


def _expose_cuda_dlls() -> None:
    """CTranslate2 needs cuBLAS/cuDNN DLLs on the search path; the torch wheel ships them."""
    try:
        import torch

        lib = Path(torch.__file__).parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
    except Exception:  # noqa: BLE001
        pass


class FasterWhisperSTT(STTEngine):
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda", compute_type: str = "int8_float16", beam_size: int = 1, language: str | None = "en") -> None:
        _expose_cuda_dlls()
        from faster_whisper import WhisperModel

        if device == "auto":
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        if device == "cpu" and "float16" in compute_type:
            compute_type = "int8"
        t0 = time.perf_counter()
        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self.beam_size = beam_size
        self.language = language
        logger.info("faster-whisper {} loaded on {} ({}) in {:.1f}s", model, device, compute_type, time.perf_counter() - t0)

    def transcribe(self, audio_int16_16k: np.ndarray) -> Transcript:
        t0 = time.perf_counter()
        audio = audio_int16_16k.astype(np.float32) / 32768.0
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=False,  # we already end-point with Silero
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt="Jarvis, a voice assistant on a Windows PC. Commands like: open Spotify, play Interstellar, search files, set volume.",
        )
        texts, probs, no_speech = [], [], []
        for seg in segments:
            texts.append(seg.text)
            probs.append(np.exp(seg.avg_logprob))
            no_speech.append(seg.no_speech_prob)
        text = sanitize("".join(texts))
        conf = float(np.mean(probs)) if probs else 0.0
        if no_speech and float(np.mean(no_speech)) > 0.6 and conf < 0.5:
            text = ""
        ms = int((time.perf_counter() - t0) * 1000)
        logger.debug("stt {} ms: {!r} (conf {:.2f})", ms, text, conf)
        return Transcript(text=text, language=info.language, confidence=conf, elapsed_ms=ms)

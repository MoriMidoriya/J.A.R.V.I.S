"""Log-spaced spectrum bands for the HUD's audio-reactive rings (Jarvis' voice and the microphone).

One `BandAnalyzer` per stream: it pre-computes the FFT-bin → band mapping for a fixed frame size, then `bands()`
turns a frame into `n_bands` values in 0..1. Costs ~30 µs per call on the audio threads; the HUD interpolates the
24 bands onto its bar rings.

Why the per-band reference curve: speech energy falls ~8 dB per octave above the voice's fundamental, so with one
global dB window the bars above 1 kHz would never move (measured on Kokoro bm_george: p90 per band -28 dB at 150 Hz,
-53 dB at 1 kHz, -60 dB at 3-7 kHz). Each band is therefore normalised against where loud speech *in that band* sits,
which gives an equaliser that dances across the whole ring instead of only on the left.
"""

from __future__ import annotations

import itertools

import numpy as np

WINDOW_DB = 36.0  # dynamic range shown per band: reference-30 dB → 0, reference+6 dB → 1


def _reference_db(fc: float) -> float:
    """Loud-speech level (≈ p90) expected in a band centred at `fc` Hz, 0 dBFS = full-scale sine."""
    return max(-60.0, -26.0 - 8.0 * np.log2(max(fc, 180.0) / 180.0))


class BandAnalyzer:
    def __init__(self, sample_rate: int, frame: int, n_bands: int = 24, fmin: float = 80.0, fmax: float = 8000.0, gain_db: float = 0.0) -> None:
        self.n = frame
        self.n_bands = n_bands
        self.window = np.hanning(frame).astype(np.float32)
        freqs = np.fft.rfftfreq(frame, 1.0 / sample_rate)
        edges = np.geomspace(fmin, min(fmax, sample_rate / 2 - 1), n_bands + 1)
        idx = np.searchsorted(freqs, edges)
        self.slices: list[tuple[int, int]] = []
        floor = []
        for a, b in itertools.pairwise(idx):
            b = int(min(max(b, a + 1), len(freqs)))  # every band owns at least one bin (low bands at coarse resolution)
            a = int(min(a, b - 1))
            self.slices.append((a, b))
            fc = float(np.sqrt(max(freqs[a], 1.0) * freqs[min(b, len(freqs) - 1)]))
            floor.append(_reference_db(fc) - 30.0 - gain_db)
        self.floor = np.array(floor, dtype=np.float32)

    def bands(self, x: np.ndarray) -> np.ndarray:
        """`x`: mono float32 in -1..1 (any length; cropped/zero-padded to the frame). Returns float32[n_bands] in 0..1."""
        if x.dtype != np.float32:
            x = x.astype(np.float32)
        if len(x) < self.n:
            x = np.pad(x, (0, self.n - len(x)))
        elif len(x) > self.n:
            x = x[-self.n :]
        spec = np.abs(np.fft.rfft(x * self.window)) * (2.0 / self.n)  # amplitude: full-scale sine → 1.0
        amp = np.fromiter((spec[a:b].mean() for a, b in self.slices), dtype=np.float32, count=self.n_bands)
        db = 20.0 * np.log10(amp + 1e-6)
        return np.clip((db - self.floor) / WINDOW_DB, 0.0, 1.0).astype(np.float32)

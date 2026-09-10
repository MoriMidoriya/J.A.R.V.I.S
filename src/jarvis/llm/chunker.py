"""Splits a token stream into speakable sentences as early as possible (for streaming TTS)."""

from __future__ import annotations

import re

# Sentence end: . ! ? … followed by space/newline, but not inside decimals ("3.5"), abbreviations or ellipses.
_BOUNDARY = re.compile(r"(?<=[.!?…])[\"')\]]?\s+(?=[A-Z0-9\"'(\[]|$)")
_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Sr|Jr|St|vs|etc|e\.g|i\.e|No|a\.m|p\.m)\.$", re.IGNORECASE)


class SentenceChunker:
    def __init__(self, min_chars: int = 12, max_chars: int = 220) -> None:
        self.buf = ""
        self.min_chars = min_chars
        self.max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            m = _BOUNDARY.search(self.buf)
            if m is None:
                break
            cand = self.buf[: m.start() + (1 if self.buf[m.start() : m.start() + 1] in "\"')]" else 0)].strip()
            rest = self.buf[m.end() :]
            if len(cand) < self.min_chars or _ABBREV.search(cand) or re.search(r"\d\.$", cand):
                # too short / abbreviation — keep accumulating unless more text is waiting
                if len(rest) > 0 and len(cand) >= 3 and not _ABBREV.search(cand):
                    out.append(cand)
                    self.buf = rest
                    continue
                break
            out.append(cand)
            self.buf = rest
        # Very long run-on without punctuation: split at a comma/semicolon.
        if len(self.buf) > self.max_chars:
            cut = max(self.buf.rfind(", "), self.buf.rfind("; "), self.buf.rfind(" — "))
            if cut > self.min_chars:
                out.append(self.buf[: cut + 1].strip())
                self.buf = self.buf[cut + 1 :].lstrip()
        return [o for o in out if o]

    def flush(self) -> list[str]:
        s = self.buf.strip()
        self.buf = ""
        return [s] if s else []

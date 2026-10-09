"""Fast-path intents that skip the LLM entirely (sub-second replies, deterministic).

Greetings ("daddy's home"), stop/cancel, thanks, and yes/no classification for confirmations.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psutil

from jarvis.config import Settings
from jarvis.llm.context import day_part

Kind = Literal["greeting", "stop", "thanks", "wake_only", "affirm", "deny"]


@dataclass
class Intent:
    kind: Kind
    reply: str | None = None


# Whisper often renders the tail of "…Jarvis" as a short fragment ("It's", "Is", "His") or hears "service" for
# "Jarvis". Tolerate up to two such fragments before a greeting.
_LEAD_FRAGMENT = r"(?:(?:it'?s|is|his|hey|so|oh|um|uh|and|jarvis|service|this|yes)\W+){0,2}"

GREETING_RX = re.compile(
    r"^\W*" + _LEAD_FRAGMENT + r"(?:"
    r"(?:daddy|papa|dad|boss|master|your (?:master|creator))(?:'?s| is)? (?:home|back|here)|"
    r"i(?:'?m| am) (?:home|back)|"
    r"(?:good |g')?(?:morning|afternoon|evening|night)(?: to you)?(?:,? jarvis)?|"
    r"(?:hello|hi|hey|yo|greetings|wake up|you there|are you there|you up|status report)(?:,? (?:jarvis|there|buddy|mate))?"
    r")\W*$",
    re.IGNORECASE,
)
STOP_RX = re.compile(r"^\W*(?:stop|cancel|never ?mind|nevermind|forget it|shut up|quiet|silence|enough|that'?s enough|abort|be quiet|hush)(?:,? jarvis)?[\s.!]*$", re.IGNORECASE)
THANKS_RX = re.compile(r"^\W*(?:thanks|thank you|cheers|ta|nice one|good job|well done|brilliant|perfect|great)(?:,? jarvis)?(?: (?:very |so )?much)?[\s.!]*$", re.IGNORECASE)
AFFIRM_RX = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|affirmative|confirm|confirmed|please do|absolutely|proceed|correct|right|aye)(?:,? (?:please|jarvis|sir))?[\s.!]*$", re.IGNORECASE)
DENY_RX = re.compile(r"^\W*(?:no|nope|nah|negative|don'?t|do not|cancel|stop|abort|never ?mind|leave it|not now|hold on|wait)(?:,? (?:jarvis|thanks|thank you))?[\s.!]*$", re.IGNORECASE)

# {title} = "sir", {part} = morning/afternoon/evening, {extra} = optional context sentence
GREETINGS = {
    "morning": [
        "Good morning, {title}. {extra}What can I do for you?",
        "Good morning, {title}. Welcome back. {extra}How may I be of service?",
        "Morning, {title}. Systems are online and I am at your disposal. {extra}",
        "Good morning, {title}. I trust you slept well. {extra}What shall we tackle first?",
    ],
    "afternoon": [
        "Good afternoon, {title}. Welcome home. {extra}What can I do for you?",
        "Good afternoon, {title}. {extra}How may I assist?",
        "Welcome back, {title}. It is a fine afternoon for getting things done. {extra}What do you need?",
        "Good afternoon, {title}. All systems nominal. {extra}At your service.",
    ],
    "evening": [
        "Good evening, {title}. Welcome home. {extra}What can I do for you?",
        "Good evening, {title}. {extra}How may I be of service tonight?",
        "Welcome back, {title}. I hope the day treated you well. {extra}What do you need?",
        "Good evening, {title}. The house is in order and so am I. {extra}What shall it be?",
    ],
    "night": [
        "Good evening, {title}. Rather late, isn't it? {extra}What can I do for you?",
        "Welcome home, {title}. Burning the midnight oil again, I see. {extra}How can I help?",
        "Good evening, {title}. {extra}I am here if you need anything before you turn in.",
    ],
}

STOP_REPLIES = ["Very well, {title}.", "Of course, {title}.", "As you wish.", "Understood."]
THANKS_REPLIES = ["You're welcome, {title}.", "Always a pleasure, {title}.", "At your service, {title}.", "Anytime, {title}."]
WAKE_ONLY_REPLIES = ["Yes, {title}?", "{title_cap}?", "At your service, {title}.", "I'm listening, {title}."]


def _extra_context() -> str:
    bits: list[str] = []
    b = psutil.sensors_battery()
    if b and not b.power_plugged and b.percent <= 25:
        bits.append(f"Battery is at {int(b.percent)} percent; you may want the charger.")
    return (" ".join(bits) + " ") if bits else ""


def _fill(template: str, s: Settings, extra: str = "") -> str:
    title = s.assistant.user_title
    out = template.format(title=title, title_cap=title.capitalize(), part=day_part(), extra=extra)
    return re.sub(r"\s{2,}", " ", out).strip()


def match(text: str, s: Settings) -> Intent | None:
    t = text.strip()
    if not t:
        return Intent("wake_only", _fill(random.choice(WAKE_ONLY_REPLIES), s))
    if GREETING_RX.match(t):
        part = day_part(datetime.now())
        return Intent("greeting", _fill(random.choice(GREETINGS[part]), s, _extra_context()))
    if STOP_RX.match(t):
        return Intent("stop", _fill(random.choice(STOP_REPLIES), s))
    if THANKS_RX.match(t):
        return Intent("thanks", _fill(random.choice(THANKS_REPLIES), s))
    return None


def classify_yes_no(text: str) -> bool | None:
    t = text.strip()
    if AFFIRM_RX.match(t):
        return True
    if DENY_RX.match(t):
        return False
    low = t.lower()
    if any(w in low for w in ("yes", "yeah", "go ahead", "do it", "confirm", "sure")) and not any(w in low for w in ("no", "don't", "not")):
        return True
    if any(w in low for w in ("no", "don't", "do not", "cancel", "stop", "never")):
        return False
    return None

"""The agent loop: persona + context → LLM (streaming) → tools → LLM … → final spoken reply."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from loguru import logger

from jarvis.config import Settings, load_persona
from jarvis.events import AssistantFinal, AssistantToken, EventBus, Metrics
from jarvis.llm.context import build_context, build_stable_context
from jarvis.llm.router import LLMRouter
from jarvis.tools.registry import ToolRegistry

TextCB = Callable[[str], Awaitable[None] | None]

# Replies that *claim* an action when no tool was called are bluffs. Three shapes, each optionally after a short
# preamble ("Apologies, sir. …", "Of course, sir — …"):
#   "Opening X…"                       present participle
#   "I'll move / let me check …"       announcement in the future tense
#   "Done, sir: …", "Are you certain…" claimed completion / a hand-rolled confirmation (the tool tier does that)
_PREAMBLE = (
    r"(?:(?:my\s+)?(?:apologies|of course|certainly|right away|straight away|very well|at once|understood|indeed|yes|"
    r"absolutely|as you wish|immediately|one moment)\b[^.!?\n]{0,30}(?:[.!?,:;—–-]+\s*|\s+))?"
)
_ING = (
    r"opening|launching|starting|closing|quitting|playing|searching|looking|finding|locating|setting|muting|unmuting|turning|"
    r"locking|shutting|restarting|sleeping|taking|showing|switching|adjusting|increasing|decreasing|lowering|raising|powering|"
    r"going\s+(?:offline|silent|quiet|dark)|hiding|dimming|folding|moving|enabling|disabling|activating|deactivating|entering|"
    r"engaging|resizing|enlarging|shrinking|returning|putting|changing|updating|configuring|sending|creating|adding|removing|"
    r"deleting|scheduling|running|checking|scanning|bringing|docking|repositioning|relocating|brightening|stopping|pausing|"
    r"resuming|minimi[sz]ing|maximi[sz]ing|focusing|typing|pressing|capturing"
)
_BASE = (
    r"open|launch|start|close|quit|play|search|look|find|locate|set|mute|unmute|turn|lock|shut|restart|sleep|take|show|switch|"
    r"adjust|increase|decrease|lower|raise|power|go\s+(?:offline|silent|quiet|dark)|hide|dim|fold|move|enable|disable|activate|"
    r"deactivate|enter|engage|resize|enlarge|shrink|return|put|change|update|configure|send|create|add|remove|delete|schedule|"
    r"run|check|scan|bring|dock|reposition|relocate|brighten|stop|pause|resume|minimi[sz]e|maximi[sz]e|focus|type|press|capture|"
    r"make|get|do\s+that|handle|take\s+care|speak|talk|silence|stay\s+(?:quiet|silent)|keep\s+(?:quiet|silent)"
)
# Claimed state changes ("Text mode off, sir", "Voice back on", "Muted") — these are what a tool would have said.
_STATE_CLAIMS = (
    r"text(?:-only|\s+only)?\s+mode|voice\s+mode|voice\s+(?:back\s+)?(?:on|off)|muted|unmuted|silenced|silent\s+mode|quiet\s+mode|"
    r"auto-?hide\s+(?:on|off)|full\s+interface\s+(?:up|on)|hud\s+(?:hidden|shown)"
)
ACTION_OPENERS = re.compile(
    rf"^\W*{_PREAMBLE}(?:(?:{_ING})\b|(?:i(?:'ll|\s+will|\s+shall|\s+am\s+going\s+to|'m\s+going\s+to)|let\s+me|allow\s+me\s+to)\s+(?:{_BASE})\b|"
    rf"(?:{_STATE_CLAIMS})\b|"
    r"done\b|completed\b|finished\b|all\s+set\b|consider\s+it\s+done|are\s+you\s+(?:certain|sure)|shall\s+i\s+(?:proceed|go\s+ahead))",
    re.IGNORECASE,
)
_PREAMBLE_RE = re.compile(rf"^\W*{_PREAMBLE}", re.IGNORECASE)
NUDGE = (
    "SYSTEM CHECK: You announced an action but called no tool, so nothing happened. "
    "Call the appropriate tool now with the right arguments. Do not reply with text."
)


class _Gate:
    """Streams text through immediately, except when the reply opens like an action ("Opening X…") and no
    tool has run yet — then it buffers so the agent can verify a tool call actually happened."""

    def __init__(self, emit: Callable[[str], Awaitable[None]], guard: bool) -> None:
        self._emit = emit
        self._buf = ""
        self._decided = not guard
        self.held = False

    async def feed(self, delta: str) -> None:
        if self._decided and not self.held:
            await self._emit(delta)
            return
        self._buf += delta
        if self._decided:
            return
        buf = self._buf
        if len(buf) < 28 and buf.count(" ") < 4 and not buf.rstrip().endswith((".", "!", "?")):
            return
        m = _PREAMBLE_RE.match(buf)
        if m and re.search(r"[a-z]", buf[: m.end()], re.IGNORECASE) and len(buf) - m.end() < 16 and len(buf) < 90:
            return  # "Apologies, sir. I'll" so far — wait for what follows before deciding
        await self._decide()

    async def _decide(self) -> None:
        self._decided = True
        self.held = bool(ACTION_OPENERS.match(self._buf))
        if not self.held:
            await self._emit(self._buf)
            self._buf = ""

    async def settle(self) -> None:
        """The stream ended: decide on whatever was buffered (a short reply may never have crossed the threshold)."""
        if not self._decided and self._buf:
            await self._decide()

    async def flush(self) -> None:
        if self._buf:
            await self._emit(self._buf)
            self._buf = ""

    def discard(self) -> None:
        self._buf = ""


class Agent:
    def __init__(self, settings: Settings, router: LLMRouter, registry: ToolRegistry, bus: EventBus) -> None:
        self.s = settings
        self.router = router
        self.registry = registry
        self.bus = bus
        self.history: list[dict[str, Any]] = []
        self.model_override: str | None = None
        self._restore_history()

    # ---- history -------------------------------------------------------------------------------
    def _trim(self) -> None:
        # Keep roughly the last N exchanges, always cutting at a user message so no tool exchange is left headless
        # (an orphaned `tool` message confuses the model and some APIs reject it).
        keep = self.s.memory.history_turns * 2
        if len(self.history) <= keep:
            return
        cut = len(self.history) - keep
        while cut < len(self.history) and self.history[cut].get("role") != "user":
            cut += 1
        self.history = self.history[cut:]

    def reset(self) -> None:
        """Forget the current conversation — also across restarts (a `system` marker row stops the resume there)."""
        self.history.clear()
        try:
            from jarvis.memory.store import get_store

            get_store().log_message("system", "[conversation reset]")
        except Exception as e:  # noqa: BLE001
            logger.debug("could not persist reset marker: {}", e)

    def _restore_history(self) -> None:
        """Continue where we left off: reload the recent conversation from SQLite if it is fresh enough.

        Each turn is stored as user text, (optionally) the tool exchange as one JSON `tool` row — the assistant's
        tool_calls message plus the tool results — and the final assistant text. Restoring the tool exchange matters:
        a history of "move the reactor" → "Done, sir" pairs *without* the tool calls between them teaches a small model
        to answer settings/action requests with text and skip the tool. A `system` row (written by `reset()`) is a hard
        boundary: nothing before it is restored.
        """
        hours = self.s.memory.resume_hours
        if hours <= 0:
            return
        try:
            from jarvis.memory.store import get_store

            rows = get_store().recent_messages(limit=self.s.memory.history_turns * 3)
        except Exception as e:  # noqa: BLE001
            logger.debug("history restore skipped: {}", e)
            return
        for i in range(len(rows) - 1, -1, -1):  # keep only what came after the last reset marker
            if rows[i]["role"] == "system":
                rows = rows[i + 1 :]
                break
        if not rows:
            return
        last = datetime.fromisoformat(rows[-1]["ts"])
        age_h = (datetime.now() - last).total_seconds() / 3600
        if age_h > hours:
            logger.debug("previous conversation is {:.1f} h old; starting fresh", age_h)
            return
        history: list[dict[str, Any]] = []
        for r in rows:
            if r["role"] in ("user", "assistant"):
                history.append({"role": r["role"], "content": r["text"]})
            elif r["role"] == "tool":
                try:
                    msgs = json.loads(r["text"])
                except ValueError:
                    continue
                if isinstance(msgs, list):
                    history.extend(m for m in msgs if isinstance(m, dict) and m.get("role") in ("assistant", "tool"))
        while history and history[0].get("role") != "user":  # the window may have cut into a turn
            history.pop(0)
        self.history = history
        self._trim()
        logger.info("resumed conversation: {} messages, last one {:.0f} min ago", len(self.history), age_h * 60)

    def _persist(self, user_text: str, assistant_text: str, model: str, tool_exchange: list[dict[str, Any]] | None = None) -> None:
        try:
            from jarvis.memory.store import get_store

            store = get_store()
            store.log_message("user", user_text)
            if tool_exchange:
                store.log_message("tool", json.dumps(tool_exchange, ensure_ascii=False, default=str), meta=model)
            store.log_message("assistant", assistant_text, meta=model)
        except Exception as e:  # noqa: BLE001
            logger.debug("could not persist turn: {}", e)

    # ---- main entry ----------------------------------------------------------------------------
    async def respond(
        self,
        text: str,
        *,
        image_b64: str | None = None,
        image_mime: str = "image/png",
        on_text: TextCB | None = None,
        model: str | None = None,
    ) -> str:
        """Run one user turn to completion. Streams reply text via `on_text` and the event bus."""
        t_start = time.perf_counter()

        async def emit_text(delta: str) -> None:
            await self.bus.emit(AssistantToken(text=delta))
            if on_text:
                r = on_text(delta)
                if r is not None and hasattr(r, "__await__"):
                    await r

        if image_b64 and not model and not self.model_override:
            return await self._vision_turn(text, image_b64, image_mime, emit_text, t_start)

        chosen = model or self.model_override or self.s.llm.primary
        system = {"role": "system", "content": load_persona(self.s, build_stable_context())}
        user_msg = self._user_message(f"[{build_context()}]\n{text}", image_b64, image_mime)
        messages: list[dict[str, Any]] = [system, *self.history, user_msg]

        final_text = ""
        first_token_ms: int | None = None
        turn_messages: list[dict[str, Any]] = [user_msg]
        tools_called = 0
        nudged = False

        for round_no in range(self.s.llm.max_tool_rounds + 1):
            tools = self.registry.schemas() if round_no < self.s.llm.max_tool_rounds else None
            # Until a tool has run, hold back replies that *sound* like actions so we can verify one happened.
            gate = _Gate(emit_text, guard=(tools_called == 0))
            result = await self.router.stream_with_fallback(messages, tools=tools, model=chosen, on_text=gate.feed)
            if first_token_ms is None:
                first_token_ms = result.first_token_ms
            logger.debug(
                "llm round {} model={} first_token={} ms total={} ms text={!r} tool_calls={}",
                round_no, result.model, result.first_token_ms, result.total_ms, result.text[:80], [tc.name for tc in result.tool_calls],
            )

            if not result.tool_calls:
                await gate.settle()
                if gate.held and tools is not None:
                    if not nudged:
                        # The model bluffed ("Opening X, sir.") without calling anything. Nudge once.
                        logger.warning("model announced an action without a tool call; nudging")
                        nudged = True
                        gate.discard()
                        messages.append({"role": "assistant", "content": result.text})
                        messages.append({"role": "user", "content": NUDGE})
                        continue
                    # Still only talk after the nudge: don't speak a promise nothing will keep (and don't teach it to
                    # the next turns via the history) — admit it instead.
                    logger.warning("model kept announcing without a tool call: {!r}", result.text[:80])
                    gate.discard()
                    final_text = f"I'm afraid that didn't go through, {self.s.assistant.user_title} — could you put it another way?"
                    await emit_text(final_text)
                    break
                await gate.flush()
                final_text = result.text
                break

            gate.discard()  # any preamble text before a tool call is not spoken
            tools_called += len(result.tool_calls)
            assistant_msg = result.as_assistant_message()
            messages.append(assistant_msg)
            turn_messages.append(assistant_msg)
            direct_reply: str | None = None
            for tc in result.tool_calls:
                outcome = await self.registry.run(tc.name, tc.arguments, call_id=tc.id)
                tool_msg = {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": outcome.content}
                messages.append(tool_msg)
                turn_messages.append(tool_msg)
                spec = self.registry.get(tc.name)
                if spec and spec.speak_result and outcome.ok and len(result.tool_calls) == 1:
                    direct_reply = outcome.content
            if direct_reply:
                # The tool's output is already the answer (e.g. describe_screen): speak it, skip another LLM round.
                await emit_text(direct_reply)
                final_text = direct_reply
                break
        else:
            final_text = result.text or "I've done what I can, sir."

        if not final_text.strip():
            final_text = "Done, sir."
            await emit_text(final_text)

        # Keep the turn in context and on disk alike: user, any tool exchange, final assistant text.
        self.history.extend(turn_messages)
        self.history.append({"role": "assistant", "content": final_text})
        self._trim()
        self._persist(text, final_text, chosen, tool_exchange=turn_messages[1:])

        total_ms = int((time.perf_counter() - t_start) * 1000)
        await self.bus.emit(AssistantFinal(text=final_text))
        await self.bus.emit(Metrics(model=chosen, llm_first_token_ms=first_token_ms, total_ms=total_ms))
        logger.info("turn done in {} ms (first token {} ms) via {}", total_ms, first_token_ms, chosen)
        return final_text

    async def _vision_turn(self, text: str, image_b64: str, image_mime: str, emit_text: TextCB, t_start: float) -> str:
        """An image was attached (HUD drop/paste): one tool-free round on the vision model, with the VRAM swap.

        The 7B vision model is slow with a long tool schema and poor at calling tools, so it only gets a short persona
        and the image. The exchange is still recorded in history (as text) so the chat model knows what was discussed.
        """
        question = text.strip() or "What do you see in this image?"
        system = {
            "role": "system",
            "content": (
                f"You are J.A.R.V.I.S., Tony Stark's AI, speaking to the user whom you address as '{self.s.assistant.user_title}'. "
                "Describe or answer questions about the attached image concisely and concretely, reading any important text verbatim. "
                "At most four sentences; this will be read aloud."
            ),
        }
        messages = [system, self._user_message(question, image_b64, image_mime)]
        try:
            async with self.router.vision_swap() as vision_model:
                result = await self.router.stream(messages, model=vision_model, on_text=emit_text, max_tokens=300, temperature=0.2, timeout=180)
        except Exception as e:  # noqa: BLE001
            logger.exception("vision turn failed")
            final_text = f"I'm afraid I couldn't analyse the image, {self.s.assistant.user_title}: {e}"
            await emit_text(final_text)
            vision_model = self.s.llm.vision or "?"
            first_token_ms = None
        else:
            final_text = result.text.strip() or f"I could not make anything out, {self.s.assistant.user_title}."
            first_token_ms = result.first_token_ms
        self.history.append({"role": "user", "content": f"[sent an image] {question}"})
        self.history.append({"role": "assistant", "content": final_text})
        self._trim()
        self._persist(f"[sent an image] {question}", final_text, vision_model)
        total_ms = int((time.perf_counter() - t_start) * 1000)
        await self.bus.emit(AssistantFinal(text=final_text))
        await self.bus.emit(Metrics(model=vision_model, llm_first_token_ms=first_token_ms, total_ms=total_ms))
        logger.info("vision turn done in {} ms (first token {} ms) via {}", total_ms, first_token_ms, vision_model)
        return final_text

    @staticmethod
    def _user_message(text: str, image_b64: str | None, mime: str) -> dict[str, Any]:
        if not image_b64:
            return {"role": "user", "content": text}
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text or "What do you see?"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }

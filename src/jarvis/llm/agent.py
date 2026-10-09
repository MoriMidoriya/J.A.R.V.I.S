"""The agent loop: persona + context → LLM (streaming) → tools → LLM … → final spoken reply."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from jarvis.config import Settings, load_persona
from jarvis.events import AssistantFinal, AssistantToken, EventBus, Metrics
from jarvis.llm.context import build_context, build_stable_context
from jarvis.llm.router import LLMRouter
from jarvis.tools.registry import ToolRegistry

TextCB = Callable[[str], Awaitable[None] | None]

# Reply openers that describe an action; if no tool was actually called, the model is bluffing.
ACTION_OPENERS = re.compile(
    r"^\W*(opening|launching|starting|closing|quitting|playing|searching|looking|finding|locating|setting|muting|unmuting|"
    r"turning|locking|shutting|restarting|sleeping|taking|showing|switching|adjusting|increasing|decreasing|lowering|raising)\b",
    re.IGNORECASE,
)
NUDGE = (
    "SYSTEM CHECK: You announced an action but called no tool, so nothing happened. "
    "Call the appropriate tool now with the right arguments. Do not reply with text."
)


class _Gate:
    """Streams text through immediately, except when the reply opens like an action ("Opening X…") and no
    tool has run yet — then it buffers so the agent can verify a tool call actually happened."""

    def __init__(self, emit: Callable[[str], Awaitable[None]], guard: bool) -> None:
        self._emit = emit
        self._guard = guard
        self._buf = ""
        self._decided = not guard
        self.held = False

    async def feed(self, delta: str) -> None:
        if self._decided and not self.held:
            await self._emit(delta)
            return
        self._buf += delta
        if not self._decided and (len(self._buf) >= 28 or self._buf.count(" ") >= 4 or self._buf.rstrip().endswith((".", "!", "?"))):
            self._decided = True
            self.held = bool(ACTION_OPENERS.match(self._buf))
            if not self.held:
                await self._emit(self._buf)
                self._buf = ""

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

    # ---- history -------------------------------------------------------------------------------
    def _trim(self) -> None:
        # Keep the last N user/assistant exchanges; drop tool chatter from older turns.
        keep = self.s.memory.history_turns * 2
        if len(self.history) > keep:
            self.history = self.history[-keep:]

    def reset(self) -> None:
        self.history.clear()

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

        chosen = model or self.model_override or (self.s.llm.vision if image_b64 and self.s.llm.vision else self.s.llm.primary)
        system = {"role": "system", "content": load_persona(self.s, build_stable_context())}
        user_msg = self._user_message(f"[{build_context()}]\n{text}", image_b64, image_mime)
        messages: list[dict[str, Any]] = [system, *self.history, user_msg]

        async def emit_text(delta: str) -> None:
            await self.bus.emit(AssistantToken(text=delta))
            if on_text:
                r = on_text(delta)
                if r is not None and hasattr(r, "__await__"):
                    await r

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
                if gate.held and not nudged and tools is not None:
                    # The model bluffed ("Opening X, sir.") without calling anything. Nudge once.
                    logger.warning("model announced an action without a tool call; nudging")
                    nudged = True
                    gate.discard()
                    messages.append({"role": "assistant", "content": result.text})
                    messages.append({"role": "user", "content": NUDGE})
                    continue
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

        # Persist the turn (user, any tool exchange, final assistant text).
        self.history.extend(turn_messages)
        self.history.append({"role": "assistant", "content": final_text})
        self._trim()

        total_ms = int((time.perf_counter() - t_start) * 1000)
        await self.bus.emit(AssistantFinal(text=final_text))
        await self.bus.emit(Metrics(model=chosen, llm_first_token_ms=first_token_ms, total_ms=total_ms))
        logger.info("turn done in {} ms (first token {} ms) via {}", total_ms, first_token_ms, chosen)
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

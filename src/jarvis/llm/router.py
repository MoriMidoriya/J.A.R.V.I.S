"""LiteLLM-backed model router. One `stream()` call works for Ollama, Anthropic, OpenAI, Gemini, Groq, OpenRouter…

Model strings follow LiteLLM conventions; `ollama/<model>` is rewritten to `ollama_chat/<model>` so native tool
calling is used.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from jarvis.config import Settings

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


@dataclass
class ToolCallReq:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCallReq] = field(default_factory=list)
    first_token_ms: int | None = None
    total_ms: int = 0
    model: str = ""

    def as_assistant_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments or "{}"}}
                for tc in self.tool_calls
            ]
        return msg


class ThinkFilter:
    """Strips <think>…</think> blocks from a token stream (Qwen3 & friends) and forwards clean text."""

    def __init__(self) -> None:
        self.in_think = False
        self.buf = ""

    def feed(self, delta: str) -> str:
        self.buf += delta
        out = ""
        while self.buf:
            if self.in_think:
                end = self.buf.find("</think>")
                if end == -1:
                    self.buf = ""  # discard thinking
                    return out
                self.buf = self.buf[end + len("</think>") :].lstrip("\n")
                self.in_think = False
                continue
            start = self.buf.find("<think>")
            if start == -1:
                # Keep a small tail in case a tag is split across chunks.
                if "<" in self.buf[-7:]:
                    cut = self.buf.rfind("<")
                    out += self.buf[:cut]
                    self.buf = self.buf[cut:]
                    return out
                out += self.buf
                self.buf = ""
                return out
            out += self.buf[:start]
            self.buf = self.buf[start + len("<think>") :]
            self.in_think = True
        return out

    def flush(self) -> str:
        s = "" if self.in_think else self.buf
        self.buf = ""
        return s


class LLMRouter:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._litellm: Any = None

    # ---- setup ---------------------------------------------------------------------------------
    def _lib(self) -> Any:
        if self._litellm is None:
            t0 = time.perf_counter()
            import litellm

            litellm.suppress_debug_info = True
            litellm.drop_params = True  # silently drop params a provider doesn't support
            litellm.set_verbose = False
            self._litellm = litellm
            logger.debug("litellm imported in {:.0f} ms", (time.perf_counter() - t0) * 1000)
        return self._litellm

    def resolve(self, model: str) -> tuple[str, dict[str, Any]]:
        """Map our model string to (litellm_model, extra kwargs)."""
        kwargs: dict[str, Any] = {}
        if model.startswith("ollama/"):
            model = "ollama_chat/" + model.split("/", 1)[1]
        if model.startswith("ollama_chat/"):
            kwargs["api_base"] = self.s.llm.ollama_base_url
            kwargs["num_ctx"] = self.s.llm.ollama_num_ctx
            kwargs["keep_alive"] = self.s.llm.ollama_keep_alive
            # Disable chain-of-thought for voice latency (Qwen3, DeepSeek-R1, gpt-oss...). Ollama >= 0.9.
            kwargs["think"] = False
        return model, kwargs

    @staticmethod
    def is_local(model: str) -> bool:
        return model.startswith(("ollama/", "ollama_chat/"))

    @staticmethod
    def wants_no_think(model: str) -> bool:
        # Soft switch for Qwen3 served by providers that don't expose a think flag (OpenRouter, Groq...).
        return "qwen3" in model.lower() and not model.startswith(("ollama/", "ollama_chat/"))

    # ---- streaming completion ------------------------------------------------------------------
    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        on_text: Callable[[str], Awaitable[None] | None] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        litellm = self._lib()
        model = model or self.s.llm.primary
        lm, extra = self.resolve(model)
        if self.wants_no_think(model) and messages and messages[0]["role"] == "system":
            messages = [{**messages[0], "content": messages[0]["content"] + "\n\n/no_think"}, *messages[1:]]

        t0 = time.perf_counter()
        first: int | None = None
        chunks: list[Any] = []
        filt = ThinkFilter()
        text = ""

        async def push(s: str) -> None:
            nonlocal text
            if not s:
                return
            text += s
            if on_text:
                r = on_text(s)
                if r is not None and hasattr(r, "__await__"):
                    await r

        resp = await litellm.acompletion(
            model=lm,
            messages=messages,
            tools=tools or None,
            stream=True,
            max_tokens=max_tokens or self.s.llm.max_tokens,
            temperature=self.s.llm.temperature if temperature is None else temperature,
            timeout=self.s.llm.request_timeout_s,
            **extra,
        )
        async for chunk in resp:
            chunks.append(chunk)
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                if first is None:
                    first = int((time.perf_counter() - t0) * 1000)
                await push(filt.feed(delta.content))
        await push(filt.flush())

        tool_calls: list[ToolCallReq] = []
        try:
            full = litellm.stream_chunk_builder(chunks, messages=messages)
            msg = full.choices[0].message
            for i, tc in enumerate(msg.tool_calls or []):
                tool_calls.append(ToolCallReq(id=tc.id or f"call_{i}", name=tc.function.name, arguments=tc.function.arguments or "{}"))
        except Exception as e:  # noqa: BLE001
            logger.warning("stream_chunk_builder failed: {}", e)

        # Some local models emit tool calls as JSON text instead of structured calls; rescue them.
        if not tool_calls and text.strip().startswith("{") and '"name"' in text:
            try:
                obj = json.loads(text.strip())
                if isinstance(obj, dict) and "name" in obj:
                    tool_calls.append(ToolCallReq(id="call_0", name=obj["name"], arguments=json.dumps(obj.get("arguments") or obj.get("parameters") or {})))
                    text = ""
            except json.JSONDecodeError:
                pass

        return LLMResult(
            text=THINK_RE.sub("", text).strip(),
            tool_calls=tool_calls,
            first_token_ms=first,
            total_ms=int((time.perf_counter() - t0) * 1000),
            model=model,
        )

    async def complete(self, messages: list[dict[str, Any]], *, model: str | None = None, max_tokens: int = 200) -> str:
        """Non-streaming convenience for small internal calls."""
        r = await self.stream(messages, model=model, max_tokens=max_tokens)
        return r.text

    async def stream_with_fallback(self, *args: Any, **kwargs: Any) -> LLMResult:
        try:
            return await self.stream(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if not self.s.llm.fallback or kwargs.get("model") == self.s.llm.fallback:
                raise
            logger.warning("primary model failed ({}); falling back to {}", e, self.s.llm.fallback)
            kwargs["model"] = self.s.llm.fallback
            return await self.stream(*args, **kwargs)


async def _aiter_noop() -> AsyncIterator[str]:  # pragma: no cover
    if False:
        yield ""

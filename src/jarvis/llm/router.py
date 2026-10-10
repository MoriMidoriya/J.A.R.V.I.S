"""Model router. One `stream()` call works for Ollama, Anthropic, OpenAI, Gemini, Groq, OpenRouter…

`ollama/<model>` goes through our native `OllamaClient` (persistent connection, streamed tool calls, server stats —
see `llm/ollama.py` for why). Everything else goes through LiteLLM using its model-string conventions.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from jarvis.config import Settings
from jarvis.llm.ollama import OllamaClient, OllamaStats
from jarvis.tasks import spawn

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
    stats: OllamaStats | None = None  # server-side timing (local models only)

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
        self.ollama = OllamaClient(settings.llm.ollama_base_url, num_ctx=settings.llm.ollama_num_ctx, keep_alive=settings.llm.ollama_keep_alive)

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

    @asynccontextmanager
    async def vision_swap(self) -> AsyncIterator[str]:
        """Make room for the vision model and yield its name.

        qwen2.5vl:7b (~6 GB) and qwen3:8b (~6.3 GB) do not both fit in 12 GB; if Ollama is left to juggle them the
        vision model is half-offloaded to CPU and a description takes ~65 s instead of ~5 s. So: explicitly unload the
        chat model and wait until its VRAM is released, run the vision call, then re-warm the chat model in the
        background (the spoken answer does not need it).
        """
        vision = self.s.llm.vision
        if not vision:
            raise RuntimeError("No vision model is configured (llm.vision).")
        swap = self.is_local(vision) and self.is_local(self.s.llm.primary) and vision != self.s.llm.primary
        chat_name = self.s.llm.primary.split("/", 1)[1]
        if swap:
            await self.ollama.unload(chat_name)
        try:
            yield vision
        finally:
            if swap:
                spawn(self.ollama.warm(chat_name), name="ollama-rewarm")

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
        timeout: float | None = None,
    ) -> LLMResult:
        model = model or self.s.llm.primary
        opts = self.s.llm.opts_for(model)
        if temperature is None:
            temperature = self.s.llm.temperature if opts.temperature is None else opts.temperature
        kw = {
            "tools": tools, "on_text": on_text, "max_tokens": max_tokens or self.s.llm.max_tokens,
            "temperature": temperature, "timeout": timeout or self.s.llm.request_timeout_s,
        }
        if self.is_local(model):
            return await self._stream_ollama(model, messages, think=opts.think, **kw)
        return await self._stream_litellm(model, messages, **kw)

    async def _stream_ollama(self, model: str, messages: list[dict[str, Any]], *, tools, on_text, max_tokens, temperature, timeout, think: str = "off") -> LLMResult:
        t0 = time.perf_counter()
        filt = ThinkFilter()
        clean = ""
        if think == "prefill" and messages and messages[-1].get("role") != "assistant":
            # Ollama continues a trailing assistant message, so an empty think block makes the model answer directly.
            messages = [*messages, {"role": "assistant", "content": "<think>\n\n</think>\n"}]

        async def push(s: str) -> None:
            nonlocal clean
            if not s:
                return
            clean += s
            if on_text:
                r = on_text(s)
                if r is not None and hasattr(r, "__await__"):
                    await r

        async def on_delta(d: str) -> None:
            await push(filt.feed(d))

        # The raw text is rebuilt from the deltas via push() (think-filtered); only the calls/stats are needed here.
        _, raw_calls, stats, first = await self.ollama.chat_stream(
            model.split("/", 1)[1], messages, tools=tools or None, on_text=on_delta,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
            think=None if think == "on" else False,
        )
        await push(filt.flush())
        tool_calls = [
            ToolCallReq(
                id=c.get("id") or f"call_{i}",
                name=c["function"]["name"],
                arguments=a if isinstance(a := c["function"].get("arguments", {}), str) else json.dumps(a),
            )
            for i, c in enumerate(raw_calls)
        ]
        text = THINK_RE.sub("", clean).strip()
        tool_calls, text = self._rescue_text_tool_call(tool_calls, text)
        if stats.load_s > 0.5:
            logger.info("ollama (re)loaded {} in {:.1f}s", model, stats.load_s)
        logger.debug(
            "ollama {}: prompt {} tok (uncached eval {:.2f}s), gen {} tok @ {:.0f} tok/s, first token {} ms",
            model, stats.prompt_tokens, stats.prompt_eval_s, stats.gen_tokens, stats.tok_per_s, first,
        )
        return LLMResult(text=text, tool_calls=tool_calls, first_token_ms=first, total_ms=int((time.perf_counter() - t0) * 1000), model=model, stats=stats)

    @staticmethod
    def _rescue_text_tool_call(tool_calls: list[ToolCallReq], text: str) -> tuple[list[ToolCallReq], str]:
        """Some local models emit tool calls as JSON text instead of structured calls; rescue them."""
        if not tool_calls and text.strip().startswith("{") and '"name"' in text:
            try:
                obj = json.loads(text.strip())
                if isinstance(obj, dict) and "name" in obj:
                    args = obj.get("arguments") or obj.get("parameters") or {}
                    return [ToolCallReq(id="call_0", name=obj["name"], arguments=json.dumps(args))], ""
            except json.JSONDecodeError:
                pass
        return tool_calls, text

    async def _stream_litellm(self, model: str, messages: list[dict[str, Any]], *, tools, on_text, max_tokens, temperature, timeout) -> LLMResult:
        litellm = self._lib()
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
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
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

        tool_calls, text = self._rescue_text_tool_call(tool_calls, THINK_RE.sub("", text).strip())
        return LLMResult(
            text=text,
            tool_calls=tool_calls,
            first_token_ms=first,
            total_ms=int((time.perf_counter() - t0) * 1000),
            model=model,
        )

    async def complete(self, messages: list[dict[str, Any]], *, model: str | None = None, max_tokens: int = 200, timeout: float | None = None) -> str:
        """Non-streaming convenience for small internal calls."""
        r = await self.stream(messages, model=model, max_tokens=max_tokens, timeout=timeout)
        return r.text

    async def stream_with_fallback(self, *args: Any, **kwargs: Any) -> LLMResult:
        try:
            return await self.stream(*args, **kwargs)
        except Exception as e:
            if not self.s.llm.fallback or kwargs.get("model") == self.s.llm.fallback:
                raise
            logger.warning("primary model failed ({}); falling back to {}", e, self.s.llm.fallback)
            kwargs["model"] = self.s.llm.fallback
            return await self.stream(*args, **kwargs)

"""Ollama: lifecycle helpers + a native streaming chat client.

Why not LiteLLM for the local path? Measured 2026-10-09: LiteLLM adds ~2.1 s to the first token whenever its HTTP
connection has been idle > 5 s (it rebuilds its client, which on Windows is slow), plus a 2.5 s import. Talking to
Ollama's `/api/chat` directly over one persistent connection gives 40–100 ms to first token, streamed tool calls,
`think` control and server-side timing stats. LiteLLM is still used for cloud providers.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger


@dataclass
class OllamaStats:
    prompt_tokens: int = 0
    prompt_eval_s: float = 0.0  # time spent on the *uncached* part of the prompt
    load_s: float = 0.0  # > 0 means the model was (re)loaded for this request
    gen_tokens: int = 0
    gen_s: float = 0.0

    @property
    def tok_per_s(self) -> float:
        return self.gen_tokens / self.gen_s if self.gen_s else 0.0


def to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-style history → Ollama's native message format.

    - assistant `tool_calls[].function.arguments` must be an object, not a JSON string
    - tool results use `tool_name` (no `tool_call_id`)
    - multimodal user content (`image_url` data URLs) becomes `content` + `images: [base64]`
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            out.append({"role": "tool", "content": str(m.get("content", "")), "tool_name": m.get("name", "")})
        elif isinstance(m.get("content"), list):
            text_parts: list[str] = []
            images: list[str] = []
            for part in m["content"]:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    images.append(url.split(",", 1)[1] if url.startswith("data:") and "," in url else url)
            msg: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
            if images:
                msg["images"] = images
            out.append(msg)
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class OllamaClient:
    """Thin async client for `/api/chat` streaming. One instance per app; reuses a single keep-alive connection."""

    def __init__(self, base_url: str, *, num_ctx: int, keep_alive: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # keepalive_expiry: never let the pooled connection go stale between voice commands.
            limits = httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=3600)
            self._client = httpx.AsyncClient(base_url=self.base_url, limits=limits, trust_env=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], Awaitable[None] | None] | None = None,
        temperature: float = 0.6,
        max_tokens: int = 400,
        timeout: float = 60.0,
        think: bool = False,
    ) -> tuple[str, list[dict[str, Any]], OllamaStats, int | None]:
        """Returns (text, raw tool_calls, stats, first_token_ms). `on_text` receives text deltas as they stream."""
        body: dict[str, Any] = {
            "model": model,
            "messages": to_ollama_messages(messages),
            "stream": True,
            "think": think,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            body["tools"] = tools
        t0 = time.perf_counter()
        first: int | None = None
        text = ""
        tool_calls: list[dict[str, Any]] = []
        stats = OllamaStats()
        async with self._http().stream("POST", "/api/chat", json=body, timeout=httpx.Timeout(timeout, connect=5.0)) as r:
            if r.status_code != 200:
                detail = (await r.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"Ollama HTTP {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if not line:
                    continue
                j = json.loads(line)
                if "error" in j:
                    raise RuntimeError(f"Ollama error: {j['error']}")
                msg = j.get("message") or {}
                delta = msg.get("content") or ""
                calls = msg.get("tool_calls") or []
                if (delta or calls) and first is None:
                    first = int((time.perf_counter() - t0) * 1000)
                if delta:
                    text += delta
                    if on_text:
                        res = on_text(delta)
                        if res is not None and hasattr(res, "__await__"):
                            await res
                tool_calls.extend(calls)
                if j.get("done"):
                    stats = OllamaStats(
                        prompt_tokens=j.get("prompt_eval_count", 0),
                        prompt_eval_s=j.get("prompt_eval_duration", 0) / 1e9,
                        load_s=j.get("load_duration", 0) / 1e9,
                        gen_tokens=j.get("eval_count", 0),
                        gen_s=j.get("eval_duration", 0) / 1e9,
                    )
        return text, tool_calls, stats, first

    async def warm_connection(self) -> None:
        """Open the keep-alive connection ahead of the first real request."""
        try:
            await self._http().get("/api/version", timeout=3.0)
        except Exception as e:  # noqa: BLE001
            logger.debug("ollama warm_connection: {}", e)


def is_up(base_url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _launch() -> str:
    """Prefer the tray app (keeps running after we exit, like a normal install); fall back to `ollama serve`."""
    tray = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    if tray.exists():
        subprocess.Popen([str(tray)], creationflags=flags, close_fds=True)
        return f"tray app ({tray})"
    subprocess.Popen(["ollama", "serve"], creationflags=flags | subprocess.CREATE_NO_WINDOW, close_fds=True)
    return "ollama serve"


def ensure_running(base_url: str, wait_s: float = 25.0) -> bool:
    """Return True once the Ollama API answers; starts it if needed. Never raises."""
    if is_up(base_url):
        return True
    try:
        how = _launch()
    except Exception as e:  # noqa: BLE001
        logger.error("could not start Ollama: {}", e)
        return False
    logger.info("Ollama was not running; started {} — waiting for the API", how)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if is_up(base_url, timeout=1.0):
            logger.info("Ollama API up after {:.1f}s", time.time() - t0)
            return True
        time.sleep(0.5)
    logger.error("Ollama API still unreachable after {:.0f}s", wait_s)
    return False


def loaded_models(base_url: str) -> list[dict]:
    """What Ollama currently holds in memory (name, size_vram) — handy for VRAM budgeting."""
    try:
        return httpx.get(f"{base_url}/api/ps", timeout=2).json().get("models", [])
    except Exception:  # noqa: BLE001
        return []

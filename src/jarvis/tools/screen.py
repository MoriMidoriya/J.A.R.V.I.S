"""Screenshots and "what's on my screen?" via the vision model.

`describe_screen` captures the screen, downsizes it (vision models don't need 2880×1800 and tokens scale with pixels),
and asks `llm.vision` (default `ollama/qwen2.5vl:7b`). Note on VRAM: qwen2.5vl:7b (~6 GB) + qwen3:8b (~6.3 GB) do not
both fit in 12 GB, so Ollama swaps them — expect ~3–5 s extra the first time and again on the next chat turn.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from datetime import datetime
from typing import Annotated

import mss
from loguru import logger
from PIL import Image
from pydantic import Field

from jarvis.config import DATA_DIR, get_settings
from jarvis.tools.registry import ToolError, tool

SHOTS_DIR = DATA_DIR / "screenshots"
VISION_MAX_W = 1280


def capture(monitor: int = 0) -> Image.Image:
    """monitor 0 = all screens combined, 1..n = individual."""
    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            raise ToolError(f"There is no monitor {monitor}; I can see {len(sct.monitors) - 1}.")
        raw = sct.grab(sct.monitors[monitor])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _to_b64_jpeg(img: Image.Image, max_w: int = VISION_MAX_W, quality: int = 80) -> str:
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


@tool(summary="Screenshot")
def take_screenshot(monitor: Annotated[int, Field(ge=0, le=8, description="0 = all screens, 1 = primary, 2 = second…")] = 0) -> str:
    """Capture the screen to a PNG file and return its path (also opens nothing — just saves)."""
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOTS_DIR / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
    capture(monitor).save(path)
    return f"Saved screenshot to {path}"


@tool(summary="Look at screen", speak_result=True)
async def describe_screen(
    question: Annotated[str, Field(description="What to look for or answer, e.g. 'what is on my screen?', 'read the error message', 'what song is playing?'")] = "Describe what is on the screen.",
    monitor: Annotated[int, Field(ge=0, le=8, description="0 = all screens, 1 = primary…")] = 1,
) -> str:
    """Look at the screen with the vision model and answer a question about it (reads text, UI, images)."""
    from jarvis.llm.router import LLMRouter

    s = get_settings()
    if not s.llm.vision:
        raise ToolError("No vision model is configured (llm.vision).")
    t0 = time.perf_counter()
    b64 = _to_b64_jpeg(capture(monitor))
    router = _router or LLMRouter(s)
    messages = [
        {"role": "system", "content": "You describe screenshots of a Windows PC for a voice assistant. Be concise and concrete: name the apps and windows, read key text and errors verbatim, and answer the question directly in at most four sentences."},
        {"role": "user", "content": [{"type": "text", "text": question}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]},
    ]
    # Both models rarely fit in VRAM together; a half-offloaded vision model is 10× slower than a swapped one.
    swap = router.is_local(s.llm.vision) and router.is_local(s.llm.primary) and s.llm.vision != s.llm.primary
    if swap:
        await router.ollama.unload(s.llm.primary.split("/", 1)[1])
    try:
        r = await router.stream(messages, model=s.llm.vision, max_tokens=300, temperature=0.2, timeout=180)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Vision model failed: {e}") from e
    finally:
        if swap:  # bring the chat model back in the background; the spoken answer doesn't need it (speak_result)
            asyncio.get_running_loop().create_task(router.ollama.warm(s.llm.primary.split("/", 1)[1]))
    logger.info("describe_screen via {} in {:.1f}s", s.llm.vision, time.perf_counter() - t0)
    return r.text or "I could not make anything out, sir."


_router = None


def set_router(router) -> None:  # noqa: ANN001
    """Share the app's router (and its warm Ollama connection) with this module."""
    global _router
    _router = router

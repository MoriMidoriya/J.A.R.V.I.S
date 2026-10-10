"""Tool registry: `@tool` decorator → JSON schema for the LLM, validation, permission tiers, execution.

Usage:
    @tool(summary="Open {name}")
    def open_app(name: Annotated[str, Field(description="App name as spoken")]) -> str: ...

Tools may be sync (run in a worker thread) or async. Return a str (spoken-friendly) or a dict/list (JSON).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, get_type_hints

from loguru import logger
from pydantic import BaseModel, ValidationError, create_model

from jarvis.config import Settings, get_settings
from jarvis.events import ConfirmRequest, ConfirmResolved, EventBus, ToolCall, ToolResult

Tier = Literal["safe", "confirm", "dangerous"]
ConfirmHandler = Callable[[str, str], Awaitable[bool]]  # (confirm_id, prompt) -> accepted


class ToolError(Exception):
    """Raise inside a tool to return a clean, user-facing failure message to the LLM."""


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str
    params_model: type[BaseModel]
    summary: str | None = None  # format string for HUD chip, e.g. "Open {name}"
    confirm_prompt: str | None = None  # format string, e.g. "Shut down the PC in {delay} seconds?"
    is_async: bool = False
    speak_result: bool = False  # result is already user-facing prose: speak it verbatim, skip the follow-up LLM round

    def schema(self) -> dict[str, Any]:
        s = self.params_model.model_json_schema()
        s.pop("title", None)
        for p in s.get("properties", {}).values():
            p.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": s},
        }

    def render(self, template: str | None, args: dict[str, Any]) -> str:
        if not template:
            return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
        try:
            return template.format(**args)
        except (KeyError, IndexError):
            return template


@dataclass
class ToolOutcome:
    ok: bool
    content: str  # what the LLM sees (JSON or text)
    summary: str  # one-liner for the HUD / logs
    elapsed_ms: int = 0


@dataclass
class ToolRegistry:
    settings: Settings = field(default_factory=get_settings)
    bus: EventBus | None = None
    confirm_handler: ConfirmHandler | None = None
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    # ---- registration -------------------------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            logger.warning("tool {} re-registered", spec.name)
        self._tools[spec.name] = spec

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        summary: str | None = None,
        confirm_prompt: str | None = None,
        speak_result: bool = False,
    ) -> Any:
        def deco(f: Callable[..., Any]) -> Callable[..., Any]:
            spec = build_spec(f, name=name, summary=summary, confirm_prompt=confirm_prompt, speak_result=speak_result)
            self.register(spec)
            return f

        return deco(fn) if fn else deco

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        out = []
        for spec in self._tools.values():
            if self.tier_of(spec.name) == "dangerous" and not self.settings.tools.allow_shell:
                continue
            out.append(spec.schema())
        return out

    def tier_of(self, name: str) -> Tier:
        return self.settings.permissions.tier_of(name)

    # ---- execution ----------------------------------------------------------------------------
    async def run(self, name: str, raw_args: dict[str, Any] | str | None, call_id: str | None = None) -> ToolOutcome:
        call_id = call_id or uuid.uuid4().hex[:8]
        spec = self._tools.get(name)
        if spec is None:
            return ToolOutcome(False, f"Unknown tool '{name}'.", f"unknown tool {name}")

        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as e:
                return ToolOutcome(False, f"Malformed arguments JSON: {e}", f"{name}: bad args")
        raw_args = raw_args or {}

        try:
            args = spec.params_model(**raw_args).model_dump()
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            logger.warning("tool {} rejected args {}: {}", name, json.dumps(raw_args, ensure_ascii=False)[:300], msg)
            return ToolOutcome(False, f"Invalid arguments: {msg}", f"{name}: invalid args")

        summary = spec.render(spec.summary, args)
        if self.bus:
            await self.bus.emit(ToolCall(id=call_id, name=name, args=args))

        tier = self.tier_of(name)
        if tier == "dangerous" and not self.settings.tools.allow_shell:
            return self._finish(call_id, name, False, "This tool is disabled in settings.", f"{name}: disabled")
        if tier in ("confirm", "dangerous"):
            accepted = await self._confirm(call_id, spec.render(spec.confirm_prompt, args) or f"{summary}?")
            if not accepted:
                return self._finish(
                    call_id, name, False,
                    f"NOT DONE: the user declined, so {name} was NOT executed and nothing changed. "
                    "Do not retry. Acknowledge in a few words that you have not done it (e.g. 'Very well, sir, cancelled.').",
                    f"{name}: declined",
                )

        t0 = time.perf_counter()
        try:
            if spec.is_async:
                result = await spec.fn(**args)
            else:
                result = await asyncio.to_thread(spec.fn, **args)
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            ok = True
        except ToolError as e:
            content, ok = f"Failed: {e}", False
        except Exception as e:  # noqa: BLE001
            logger.exception("tool {} crashed", name)
            content, ok = f"Failed: {type(e).__name__}: {e}", False
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info("tool {} {} in {} ms -> {}", name, "ok" if ok else "FAILED", elapsed, content[:200].replace("\n", " "))
        return self._finish(call_id, name, ok, content, summary, elapsed)

    def _finish(self, call_id: str, name: str, ok: bool, content: str, summary: str, elapsed: int = 0) -> ToolOutcome:
        if self.bus:
            self.bus.emit_nowait(ToolResult(id=call_id, name=name, ok=ok, summary=summary if ok else f"{summary} — {content[:80]}"))
        return ToolOutcome(ok, content, summary, elapsed)

    async def _confirm(self, call_id: str, prompt: str) -> bool:
        if self.bus:
            await self.bus.emit(ConfirmRequest(id=call_id, prompt=prompt))
        if self.confirm_handler is None:
            logger.warning("no confirm handler set; refusing {}", prompt)
            accepted = False
        else:
            try:
                accepted = await asyncio.wait_for(self.confirm_handler(call_id, prompt), timeout=self.settings.tools.confirm_timeout_s)
            except TimeoutError:
                accepted = False
        if self.bus:
            await self.bus.emit(ConfirmResolved(id=call_id, accepted=accepted))
        return accepted


# ---- schema building ---------------------------------------------------------------------------
def build_spec(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    summary: str | None = None,
    confirm_prompt: str | None = None,
    speak_result: bool = False,
) -> ToolSpec:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, Any] = {}
    for pname, p in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        ann = hints.get(pname, str)
        default = ... if p.default is inspect.Parameter.empty else p.default
        fields[pname] = (ann, default)
    params_model = create_model(f"{fn.__name__}_params", **fields)  # type: ignore[call-overload]
    doc = inspect.getdoc(fn) or fn.__name__.replace("_", " ")
    return ToolSpec(
        name=name or fn.__name__,
        fn=fn,
        description=doc.strip(),
        params_model=params_model,
        summary=summary,
        confirm_prompt=confirm_prompt,
        is_async=inspect.iscoroutinefunction(fn),
        speak_result=speak_result,
    )


# A process-wide default registry; tool modules register into it at import time.
registry = ToolRegistry()
tool = registry.tool

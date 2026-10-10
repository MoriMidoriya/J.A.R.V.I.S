"""Typed configuration loaded from config/settings.yaml, overridable via env (JARVIS__SECTION__KEY)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"

load_dotenv(ROOT / ".env")


class AssistantCfg(BaseModel):
    name: str = "Jarvis"
    user_title: str = "sir"
    user_name: str = "sir"
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    hot_window_seconds: float = 8.0
    language: str = "en"
    home_city: str | None = None  # for weather when no city is named; null = locate by IP


class ModelOpts(BaseModel):
    """Per-model overrides (keyed by the model string in `llm.model_opts`).

    think:  off     = ask Ollama to disable chain-of-thought (Qwen3 & co. honour `think: false`)
            on      = let the model think; Ollama separates it into the `thinking` field, which Jarvis ignores
                      (models that always reason, e.g. LFM2.5-8B-A1B — `think: false` would leak `<think>` text)
            prefill = start the assistant turn with an empty `<think></think>` block so the model skips reasoning
                      (LFM2.5-2.6B: same quality on tool calls, ~4x fewer tokens per turn)
    """

    think: Literal["off", "on", "prefill"] = "off"
    temperature: float | None = None  # overrides llm.temperature for this model (e.g. LFM2.5 recommends 0.1-0.2)


class LLMCfg(BaseModel):
    primary: str = "ollama/qwen3:8b"
    fast: str | None = "ollama/qwen3:8b"
    vision: str | None = "ollama/qwen2.5vl:7b"
    fallback: str | None = None
    temperature: float = 0.6
    max_tokens: int = 400
    request_timeout_s: float = 60
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_num_ctx: int = 8192
    ollama_keep_alive: str = "30m"
    max_tool_rounds: int = 6
    model_opts: dict[str, ModelOpts] = Field(default_factory=dict)

    def opts_for(self, model: str) -> ModelOpts:
        """Exact key first, then a key that is a prefix of the model string (so `ollama/lfm2.5` covers all tags)."""
        if model in self.model_opts:
            return self.model_opts[model]
        for key, opts in self.model_opts.items():
            if model.startswith(key):
                return opts
        return ModelOpts()


class STTCfg(BaseModel):
    engine: Literal["faster_whisper", "moonshine", "openai", "groq"] = "faster_whisper"
    model: str = "large-v3-turbo"
    device: Literal["cuda", "cpu", "auto"] = "cuda"
    compute_type: str = "int8_float16"
    beam_size: int = 1


class TTSCfg(BaseModel):
    engine: Literal["kokoro", "elevenlabs", "openai"] = "kokoro"
    voice: str = "bm_george"
    speed: float = 1.0
    comm_link_fx: bool = False


class AudioCfg(BaseModel):
    input_device: int | str | None = None
    output_device: int | str | None = None
    sample_rate: int = 16000
    frame_ms: int = 80
    vad_threshold: float = 0.5
    vad_silence_ms: int = 600
    max_utterance_s: float = 15


class UICfg(BaseModel):
    enabled: bool = True
    mode: Literal["compact", "panel", "cinematic"] = "compact"
    host: str = "127.0.0.1"
    port: int = 8765
    hotkey_text_input: str = "<ctrl>+<shift>+j"
    hotkey_toggle_hud: str = "<ctrl>+<shift>+h"
    start_with_windows: bool = False


class ToolsCfg(BaseModel):
    everything_path: str = r"C:\Program Files\Everything\Everything.exe"
    allow_shell: bool = True
    confirm_timeout_s: float = 15


class MemoryCfg(BaseModel):
    db_path: str = "data/jarvis.db"
    history_turns: int = 12
    # On start, resume the conversation if the last exchange is younger than this (0 = always start fresh).
    resume_hours: float = 6


class LoggingCfg(BaseModel):
    level: str = "INFO"
    file: str = "logs/jarvis.log"


class PermissionsCfg(BaseModel):
    tiers: dict[str, list[str]] = Field(default_factory=dict)
    allowed_roots: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)

    def tier_of(self, tool_name: str) -> Literal["safe", "confirm", "dangerous"]:
        if tool_name in self.tiers.get("dangerous", []):
            return "dangerous"
        if tool_name in self.tiers.get("confirm", []):
            return "confirm"
        return "safe"

    def expanded_roots(self) -> list[Path]:
        return [Path(os.path.expandvars(p)) for p in self.allowed_roots if Path(os.path.expandvars(p)).exists()]

    def expanded_denied(self) -> list[Path]:
        return [Path(os.path.expandvars(p)) for p in self.denied_paths]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS__", env_nested_delimiter="__", extra="ignore")

    assistant: AssistantCfg = AssistantCfg()
    llm: LLMCfg = LLMCfg()
    stt: STTCfg = STTCfg()
    tts: TTSCfg = TTSCfg()
    audio: AudioCfg = AudioCfg()
    ui: UICfg = UICfg()
    tools: ToolsCfg = ToolsCfg()
    memory: MemoryCfg = MemoryCfg()
    logging: LoggingCfg = LoggingCfg()
    permissions: PermissionsCfg = PermissionsCfg()

    @property
    def db_path(self) -> Path:
        p = Path(self.memory.db_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def log_path(self) -> Path:
        p = Path(self.logging.file)
        return p if p.is_absolute() else ROOT / p


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data = _load_yaml(CONFIG_DIR / "settings.yaml")
    data["permissions"] = _load_yaml(CONFIG_DIR / "permissions.yaml")
    # BaseSettings merges env overrides on top of init kwargs.
    return Settings(**data)


def load_persona(settings: Settings, context: str) -> str:
    template = (CONFIG_DIR / "persona.md").read_text(encoding="utf-8")
    return (
        template.replace("{user_name}", settings.assistant.user_name)
        .replace("{user_title}", settings.assistant.user_title)
        .replace("{context}", context)
    )

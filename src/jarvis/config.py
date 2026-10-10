"""Typed configuration loaded from config/settings.yaml, overridable via env (JARVIS__SECTION__KEY)."""

from __future__ import annotations

import json
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


class AudioCfg(BaseModel):
    input_device: int | str | None = None
    output_device: int | str | None = None
    sample_rate: int = 16000
    frame_ms: int = 80
    vad_threshold: float = 0.5
    vad_silence_ms: int = 600
    max_utterance_s: float = 15


Corner = Literal["bottom-right", "bottom-left", "top-right", "top-left"]
TILES = ("wave", "spectrum", "system", "radar", "clock", "globe", "pipeline")  # side instruments, see hud/src/widgets.ts


class UICfg(BaseModel):
    enabled: bool = True
    mode: Literal["compact", "panel", "cinematic"] = "compact"
    host: str = "127.0.0.1"
    port: int = 8765
    hotkey_text_input: str = "<ctrl>+<shift>+j"
    hotkey_toggle_hud: str = "<ctrl>+<shift>+h"
    # ---- user preferences (editable from the HUD's settings drawer; persisted in data/ui.json, which overrides the
    # values below — see load_ui_prefs / save_ui_prefs) -------------------------------------------------------------
    # Attention behaviour (compact mode): the reactor sits dimmed in the corner; "Hey Jarvis" brings it to full
    # brightness and unfolds the last lines of conversation next to it; `linger_s` after the turn it folds and dims again.
    corner: Corner = "bottom-right"  # where the compact reactor (and the panel) dock
    idle_opacity: float = Field(0.55, ge=0.1, le=1.0)
    linger_s: float = Field(8.0, ge=0.0, le=120.0)
    reactor_size: int = Field(300, ge=200, le=400)  # px, compact mode
    show_convo: bool = True  # unfold the last lines of conversation beside the reactor while engaged
    show_status: bool = True  # the STANDBY / LISTENING word under the reactor
    auto_hide: bool = False  # hide the window completely while resting; it reappears on "Hey Jarvis" / activity
    widgets: bool = True  # master switch for the side instruments in panel/cinematic modes
    tiles: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(TILES, True))  # per-instrument switches
    cinematic_dim: float = Field(0.45, ge=0.0, le=0.9)  # how much the full-screen mode darkens the desktop behind it
    scanlines: bool = True  # cinematic mode only
    # Text-only mode: Jarvis never speaks — replies (and confirmations, reminders) appear on the HUD only. He still
    # listens. Toggled from the settings drawer, the header, the tray or by voice ("text mode" / "speak again").
    text_only: bool = False


# Keys of UICfg that the HUD may change at runtime and that are persisted to data/ui.json.
UI_PREF_KEYS = (
    "corner", "idle_opacity", "linger_s", "reactor_size", "show_convo", "show_status", "auto_hide", "widgets", "tiles", "cinematic_dim", "scanlines", "text_only",
)
UI_PREFS_PATH = DATA_DIR / "ui.json"


def ui_prefs(ui: UICfg) -> dict[str, Any]:
    """The user-editable subset of the UI config, as sent to the HUD (`hello.ui`, `ui.prefs`)."""
    d = ui.model_dump(mode="json")
    return {k: d[k] for k in UI_PREF_KEYS}


def load_ui_prefs() -> dict[str, Any]:
    """Overrides saved from the HUD's settings drawer (unknown keys ignored; a corrupt file is ignored, not fatal)."""
    try:
        data = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8")) if UI_PREFS_PATH.exists() else {}
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in UI_PREF_KEYS} if isinstance(data, dict) else {}


def save_ui_prefs(ui: UICfg) -> None:
    UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_PREFS_PATH.write_text(json.dumps(ui_prefs(ui), indent=2), encoding="utf-8")


def update_ui_prefs(ui: UICfg, patch: dict[str, Any]) -> UICfg:
    """Validated merge of a (partial) prefs patch; `tiles` merges per key. Raises pydantic.ValidationError on bad values."""
    data = ui.model_dump()
    for k, v in patch.items():
        if k not in UI_PREF_KEYS:
            continue
        if k == "tiles" and isinstance(v, dict):
            data["tiles"] = {**data["tiles"], **{t: bool(on) for t, on in v.items() if t in TILES}}
        else:
            data[k] = v
    return UICfg.model_validate(data)


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
    # Preferences changed from the HUD's settings drawer win over settings.yaml (both the window process and the
    # backend read this, so they agree on e.g. the corner at start-up).
    overrides = load_ui_prefs()
    if overrides:
        ui = data.get("ui") or {}
        if "tiles" in overrides and isinstance(ui.get("tiles"), dict):
            overrides["tiles"] = {**ui["tiles"], **overrides["tiles"]}
        data["ui"] = {**ui, **overrides}
    # BaseSettings merges env overrides on top of init kwargs.
    return Settings(**data)


def load_persona(settings: Settings, context: str) -> str:
    template = (CONFIG_DIR / "persona.md").read_text(encoding="utf-8")
    return (
        template.replace("{user_name}", settings.assistant.user_name)
        .replace("{user_title}", settings.assistant.user_title)
        .replace("{context}", context)
    )

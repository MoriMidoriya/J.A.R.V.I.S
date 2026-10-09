"""`jarvis doctor` — verifies GPU/CUDA, audio devices, Ollama, Everything, eSpeak, Node and models."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from jarvis.config import MODELS_DIR, ROOT, get_settings

console = Console()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    warn: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", hint: str = "", warn: bool = False) -> None:
        self.checks.append(Check(name, ok, detail, hint, warn))

    @property
    def failures(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.warn)


def _try_import(mod: str) -> tuple[bool, str]:
    try:
        m = importlib.import_module(mod)
        return True, getattr(m, "__version__", "")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def check_python(r: Report) -> None:
    v = sys.version_info
    r.add("Python", v[:2] == (3, 12), f"{v.major}.{v.minor}.{v.micro} @ {sys.executable}", "Expected 3.12 via `uv sync`")


def check_torch(r: Report) -> None:
    ok, ver = _try_import("torch")
    if not ok:
        r.add("PyTorch", False, ver, "uv sync")
        return
    import torch

    cuda = torch.cuda.is_available()
    detail = f"torch {torch.__version__}, cuda build {torch.version.cuda}"
    if cuda:
        name = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        free, total = torch.cuda.mem_get_info(0)
        detail += f" | {name} sm_{cap[0]}{cap[1]} | {free / 2**30:.1f}/{total / 2**30:.1f} GB free"
        # Quick kernel smoke test: Blackwell (sm_120) must actually execute, not just be detected.
        try:
            x = torch.randn(256, 256, device="cuda")
            (x @ x).sum().item()
            torch.cuda.synchronize()
        except Exception as e:  # noqa: BLE001
            r.add("PyTorch CUDA", False, f"{detail} | kernel launch FAILED: {e}", "Needs a cu128+ torch build for RTX 50-series")
            return
    r.add("PyTorch CUDA", cuda, detail, "GPU not visible to torch; check cu128 index in pyproject" if not cuda else "")


def check_ctranslate2(r: Report) -> None:
    ok, ver = _try_import("ctranslate2")
    if not ok:
        r.add("CTranslate2 (faster-whisper)", False, ver, "uv sync")
        return
    import ctranslate2

    n = ctranslate2.get_cuda_device_count()
    types = []
    try:
        types = ctranslate2.get_supported_compute_types("cuda") if n else ctranslate2.get_supported_compute_types("cpu")
    except Exception:  # noqa: BLE001
        pass
    r.add(
        "CTranslate2 CUDA",
        n > 0,
        f"v{ctranslate2.__version__}, cuda devices={n}, compute_types={sorted(types)}",
        "faster-whisper will fall back to CPU (slower but works). Check cuBLAS/cuDNN DLLs on PATH.",
        warn=n == 0,
    )


def check_onnxruntime(r: Report) -> None:
    ok, ver = _try_import("onnxruntime")
    if not ok:
        r.add("onnxruntime", False, ver, "uv sync")
        return
    import onnxruntime as ort

    r.add("onnxruntime", True, f"v{ort.__version__} providers={ort.get_available_providers()}")


def check_audio(r: Report) -> None:
    ok, ver = _try_import("sounddevice")
    if not ok:
        r.add("sounddevice", False, ver, "uv sync")
        return
    import sounddevice as sd

    try:
        devs = sd.query_devices()
        din = sd.default.device[0]
        dout = sd.default.device[1]
        inputs = [f"[{i}] {d['name']}" for i, d in enumerate(devs) if d["max_input_channels"] > 0]
        outputs = [f"[{i}] {d['name']}" for i, d in enumerate(devs) if d["max_output_channels"] > 0]
        r.add("Mic (default)", din is not None and din >= 0, f"{devs[din]['name'] if din >= 0 else 'none'}")
        r.add("Speaker (default)", dout is not None and dout >= 0, f"{devs[dout]['name'] if dout >= 0 else 'none'}")
        r.add("Audio devices", True, f"{len(inputs)} inputs, {len(outputs)} outputs (see --verbose)")
        r.inputs = inputs  # type: ignore[attr-defined]
        r.outputs = outputs  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        r.add("Audio devices", False, str(e))


def check_ollama(r: Report) -> None:
    s = get_settings()
    try:
        resp = httpx.get(f"{s.llm.ollama_base_url}/api/tags", timeout=3)
        models = [m["name"] for m in resp.json().get("models", [])]
        r.add("Ollama server", True, f"{len(models)} models: {', '.join(models) or '(none yet)'}")
        wanted = [m.split("/", 1)[1] for m in (s.llm.primary, s.llm.fast, s.llm.vision) if m and m.startswith("ollama/")]
        for w in wanted:
            have = any(m == w or m.startswith(w + ":") or m.split(":")[0] == w.split(":")[0] and m == w for m in models)
            r.add(f"  model {w}", have, "pulled" if have else "missing", f"ollama pull {w}", warn=not have)
    except Exception as e:  # noqa: BLE001
        r.add("Ollama server", False, f"{type(e).__name__}", "Start Ollama (it runs in the tray) or `winget install Ollama.Ollama`")


def check_everything(r: Report) -> None:
    s = get_settings()
    exe = Path(s.tools.everything_path)
    r.add("Everything (file index)", exe.exists(), str(exe), "winget install voidtools.Everything", warn=not exe.exists())
    try:
        from jarvis.tools import everything

        ipc = everything.ipc_ready()
        r.add("  Everything IPC instance", ipc, "running" if ipc else "not running (Jarvis auto-starts it on first search)", warn=not ipc)
    except Exception as e:  # noqa: BLE001
        r.add("  Everything IPC instance", False, str(e), warn=True)
    dll = MODELS_DIR / "everything" / "Everything64.dll"
    r.add("  Everything SDK dll", dll.exists(), str(dll), "auto-downloaded on first search", warn=not dll.exists())


def check_espeak(r: Report) -> None:
    candidates = [shutil.which("espeak-ng"), r"C:\Program Files\eSpeak NG\espeak-ng.exe"]
    found = next((c for c in candidates if c and Path(c).exists()), None)
    r.add("eSpeak NG (Kokoro fallback G2P)", found is not None, found or "not found", "winget install eSpeak-NG.eSpeak-NG", warn=found is None)


def check_node(r: Report) -> None:
    node = shutil.which("node")
    ver = ""
    if node:
        try:
            ver = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            pass
    r.add("Node.js (HUD build)", node is not None, ver or "not found", "winget install OpenJS.NodeJS.LTS", warn=node is None)
    dist = ROOT / "hud" / "dist" / "index.html"
    r.add("HUD built (hud/dist)", dist.exists(), str(dist.parent) if dist.exists() else "not built", "cd hud && npm install && npm run build", warn=not dist.exists())


def check_packages(r: Report) -> None:
    for mod, label in [
        ("openwakeword", "openWakeWord"),
        ("silero_vad", "Silero VAD"),
        ("faster_whisper", "faster-whisper"),
        ("kokoro", "Kokoro TTS"),
        ("litellm", "LiteLLM"),
        ("PySide6.QtWebEngineWidgets", "PySide6 WebEngine"),
        ("pywinauto", "pywinauto"),
        ("pycaw.pycaw", "pycaw"),
    ]:
        ok, ver = _try_import(mod)
        r.add(label, ok, ver if ok else ver[:120], "uv sync")


def check_models(r: Report) -> None:
    oww = MODELS_DIR / "openwakeword"
    has_oww = oww.exists() and any(oww.glob("hey_jarvis*.onnx"))
    r.add("openWakeWord models", has_oww, str(oww), "downloaded on first `jarvis run`", warn=not has_oww)
    hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    whisper = any(hf.glob("models--*whisper*")) if hf.exists() else False
    kokoro = any(hf.glob("models--hexgrad--Kokoro*")) if hf.exists() else False
    r.add("Whisper weights (HF cache)", whisper, str(hf), "downloaded on first use (~1.6 GB)", warn=not whisper)
    r.add("Kokoro weights (HF cache)", kokoro, str(hf), "downloaded on first use (~330 MB)", warn=not kokoro)


def run(verbose: bool = False) -> int:
    r = Report()
    for fn in (
        check_python,
        check_torch,
        check_ctranslate2,
        check_onnxruntime,
        check_packages,
        check_audio,
        check_ollama,
        check_everything,
        check_espeak,
        check_node,
        check_models,
    ):
        try:
            fn(r)
        except Exception as e:  # noqa: BLE001
            r.add(fn.__name__, False, f"{type(e).__name__}: {e}")

    t = Table(title="J.A.R.V.I.S. systems check", show_lines=False)
    t.add_column("", width=3)
    t.add_column("Component")
    t.add_column("Detail", overflow="fold")
    t.add_column("Hint", style="dim", overflow="fold")
    for c in r.checks:
        mark = "[green]OK[/]" if c.ok else ("[yellow]--[/]" if c.warn else "[red]XX[/]")
        t.add_row(mark, c.name, c.detail, "" if c.ok else c.hint)
    console.print(t)

    if verbose and hasattr(r, "inputs"):
        console.print("[bold]Input devices[/]")
        for d in r.inputs:  # type: ignore[attr-defined]
            console.print("  " + d)
        console.print("[bold]Output devices[/]")
        for d in r.outputs:  # type: ignore[attr-defined]
            console.print("  " + d)

    if r.failures:
        console.print(f"[red]{r.failures} hard failure(s).[/]")
    else:
        console.print("[green]All systems nominal, sir.[/]")
    return 1 if r.failures else 0

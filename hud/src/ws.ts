/** Typed WebSocket client for the Jarvis backend (see src/jarvis/server/hub.py for the protocol). */

export type JarvisState = "booting" | "idle" | "listening" | "thinking" | "speaking" | "confirming" | "error";

// Mirrors src/jarvis/events.py (pydantic models serialised with model_dump).
export type Mode = "compact" | "panel" | "cinematic";
export type Corner = "bottom-right" | "bottom-left" | "top-right" | "top-left";
export type TileKind = "wave" | "spectrum" | "system" | "radar" | "clock" | "globe" | "pipeline";

/** User preferences (mirrors UICfg's UI_PREF_KEYS in config.py; persisted by the backend in data/ui.json). */
export type UiPrefs = {
  corner: Corner;
  idle_opacity: number;
  linger_s: number;
  reactor_size: number;
  show_convo: boolean;
  show_status: boolean;
  auto_hide: boolean;
  widgets: boolean;
  tiles: Record<TileKind, boolean>;
  cinematic_dim: number;
  scanlines: boolean;
  text_only: boolean; // Jarvis shows his replies instead of speaking them (he still listens)
};

export type UiCommand = { type: "ui.command"; action: "show" | "hide" | "mode" | "shutdown"; mode: Mode | null };

export type SystemStats = {
  type: "system.stats";
  cpu_pct: number;
  mem_pct: number;
  vram_used_gb: number | null;
  vram_total_gb: number | null;
  net_up_kbps: number;
  net_down_kbps: number;
  battery_pct: number | null;
  plugged: boolean | null;
};

export type ServerEvent =
  | {
      type: "hello";
      state: JarvisState;
      model: string;
      user_title: string;
      mode: string;
      ui?: UiPrefs;
      hotkeys?: { text_input: string; toggle_hud: string };
      history: ServerEvent[];
      pending_confirm: { id: string; prompt: string } | null;
    }
  | { type: "ui.prefs"; prefs: UiPrefs }
  | UiCommand
  | { type: "state"; state: JarvisState }
  | { type: "wake"; score: number }
  | { type: "transcript.final"; text: string; source: "voice" | "text" }
  | { type: "assistant.token"; text: string }
  | { type: "assistant.final"; text: string }
  | { type: "audio.level"; level: number }
  | { type: "mic.level"; level: number }
  | { type: "spectrum"; source: "out" | "mic"; bands: number[] }
  | SystemStats
  | { type: "tool.call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool.result"; id: string; name: string; ok: boolean; summary: string }
  | { type: "confirm.request"; id: string; prompt: string }
  | { type: "confirm.resolved"; id: string; accepted: boolean }
  | { type: "notice"; text: string; level: "info" | "warn" | "error" }
  | { type: "reminder.due"; id: number; text: string; kind: "reminder" | "timer" }
  | { type: "metrics"; model: string; stt_ms: number | null; llm_first_token_ms: number | null; tts_first_audio_ms: number | null; total_ms: number | null };

export type ClientMessage =
  | { type: "text"; text: string }
  | { type: "image"; text: string; image_b64: string; mime: string }
  | { type: "confirm"; id: string; accepted: boolean }
  | { type: "wake" }
  | { type: "interrupt" }
  | { type: "client"; kind: "window" | "browser" }
  | { type: "ui.prefs"; prefs: Partial<Omit<UiPrefs, "tiles">> & { tiles?: Partial<Record<TileKind, boolean>> } };

type Handler = (ev: ServerEvent) => void;

export class JarvisSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private backoff = 500;
  private closed = false;
  connected = false;
  onConnection: ((up: boolean) => void) | null = null;

  constructor(private url: string = defaultUrl()) {}

  connect(): void {
    this.closed = false;
    this.open();
  }

  private open(): void {
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.backoff = 500;
      this.onConnection?.(true);
    };
    ws.onmessage = (m) => {
      let ev: ServerEvent;
      try {
        ev = JSON.parse(m.data as string) as ServerEvent;
      } catch {
        return;
      }
      for (const h of this.handlers) h(ev);
    };
    ws.onclose = () => {
      const was = this.connected;
      this.connected = false;
      if (was) this.onConnection?.(false);
      if (!this.closed) {
        setTimeout(() => this.open(), this.backoff);
        this.backoff = Math.min(this.backoff * 1.6, 5000);
      }
    };
    ws.onerror = () => ws.close();
  }

  on(h: Handler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  send(msg: ClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}

function defaultUrl(): string {
  const q = new URLSearchParams(location.search);
  const explicit = q.get("ws");
  if (explicit) return explicit;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // Served by the backend itself (http://127.0.0.1:8765) or by the Vite dev server (proxied).
  return `${proto}://${location.host}/ws`;
}

/** Settings drawer (panel / cinematic): what shows, where the reactor lives, how it behaves — plus the actions that
 *  are otherwise only on the tray (hide, modes, shut down). Values apply immediately and are sent to the backend,
 *  which validates, persists them in data/ui.json and broadcasts them to every HUD. */

import type { Corner, TileKind, UiPrefs } from "./ws";

export type PrefPatch = Partial<Omit<UiPrefs, "tiles">> & { tiles?: Partial<Record<TileKind, boolean>> };
export type Action = "hide" | "compact" | "panel" | "cinematic" | "shutdown";

export type SettingsHost = {
  prefs(): UiPrefs;
  preview(patch: PrefPatch): void; // apply locally only (slider drag)
  change(patch: PrefPatch): void; // apply locally + send
  action(a: Action): void;
  hotkeys(): { text_input: string; toggle_hud: string };
};

const TILE_LABEL: Record<TileKind, string> = {
  wave: "Audio I/O",
  spectrum: "Spectrum",
  system: "System gauges",
  radar: "Telemetry radar",
  clock: "Chrono",
  globe: "Uplink globe",
  pipeline: "Pipeline latencies",
};
const CORNERS: Corner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function keyName(hk: string): string {
  return hk.replace(/[<>]/g, "").split("+").map((p) => (p.length === 1 ? p.toUpperCase() : p[0].toUpperCase() + p.slice(1))).join("+");
}

export function initSettings(root: HTMLElement, host: SettingsHost) {
  const refreshers: Array<(p: UiPrefs) => void> = [];
  root.innerHTML = "";

  // ---- header ------------------------------------------------------------------------------
  const head = el("div", "s-head");
  head.append(el("span", "s-title", "SETTINGS"));
  const close = el("button", "hbtn", "✕");
  close.title = "Close (Esc)";
  close.onclick = () => api.close();
  head.append(close);
  root.append(head);
  const body = el("div", "s-body");
  root.append(body);

  const section = (title: string, hint?: string): HTMLElement => {
    const s = el("section", "s-sec");
    s.append(el("h3", undefined, title));
    if (hint) s.append(el("p", "s-hint", hint));
    body.append(s);
    return s;
  };
  // a <label> row makes the text clickable for its control; rows holding several buttons must not be labels
  // (a label activates its *first* labelable descendant)
  const row = (parent: HTMLElement, label: string, control: HTMLElement, hint?: string, tag: "label" | "div" = "label"): HTMLElement => {
    const r = el(tag, "s-row");
    const l = el("span", "s-label", label);
    if (hint) l.append(el("small", undefined, hint));
    r.append(l, control);
    parent.append(r);
    return r;
  };
  const toggle = (parent: HTMLElement, label: string, get: (p: UiPrefs) => boolean, set: (v: boolean) => PrefPatch, hint?: string): void => {
    const wrap = el("span", "sw");
    const input = el("input");
    input.type = "checkbox";
    wrap.append(input, el("i"));
    input.onchange = () => host.change(set(input.checked));
    refreshers.push((p) => (input.checked = get(p)));
    row(parent, label, wrap, hint);
  };
  const slider = (parent: HTMLElement, label: string, min: number, max: number, step: number, get: (p: UiPrefs) => number, set: (v: number) => PrefPatch, fmt: (v: number) => string): void => {
    const wrap = el("span", "rng");
    const input = el("input");
    input.type = "range";
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    const out = el("output");
    wrap.append(input, out);
    const show = (v: number) => {
      out.textContent = fmt(v);
      input.style.setProperty("--fill", `${((v - min) / (max - min)) * 100}%`);
    };
    input.oninput = () => {
      const v = Number(input.value);
      show(v);
      host.preview(set(v));
    };
    input.onchange = () => host.change(set(Number(input.value)));
    refreshers.push((p) => {
      const v = get(p);
      input.value = String(v);
      show(v);
    });
    row(parent, label, wrap);
  };

  // ---- overlay -------------------------------------------------------------------------------
  const ov = section("OVERLAY", "The reactor in the corner. Click-through: it never takes your mouse or keyboard.");
  const corners = el("span", "corners");
  const cornerBtns = new Map<Corner, HTMLButtonElement>();
  for (const c of CORNERS) {
    const b = el("button", `corner ${c}`);
    b.type = "button";
    b.title = c.replace("-", " ");
    b.append(el("i"));
    b.onclick = () => host.change({ corner: c });
    corners.append(b);
    cornerBtns.set(c, b);
  }
  refreshers.push((p) => cornerBtns.forEach((b, c) => b.classList.toggle("on", p.corner === c)));
  row(ov, "Corner of the screen", corners, undefined, "div");
  slider(ov, "Reactor size", 200, 400, 10, (p) => p.reactor_size, (v) => ({ reactor_size: v }), (v) => `${v} px`);
  slider(ov, "Brightness while idle", 10, 100, 5, (p) => Math.round(p.idle_opacity * 100), (v) => ({ idle_opacity: v / 100 }), (v) => `${v} %`);
  slider(ov, "Stay unfolded after a turn", 0, 60, 1, (p) => p.linger_s, (v) => ({ linger_s: v }), (v) => `${v} s`);
  toggle(ov, "Conversation beside the reactor", (p) => p.show_convo, (v) => ({ show_convo: v }), "the last three lines while he's engaged");
  toggle(ov, "Status word", (p) => p.show_status, (v) => ({ show_status: v }), "STANDBY · LISTENING · …");
  toggle(ov, "Hide completely while idle", (p) => p.auto_hide, (v) => ({ auto_hide: v }), "comes back on “Hey Jarvis”");

  // ---- full interface -------------------------------------------------------------------------
  const fi = section("FULL INTERFACE", "Panel shows up to three instruments; cinematic shows them all.");
  toggle(fi, "Instruments", (p) => p.widgets, (v) => ({ widgets: v }));
  const grid = el("div", "s-tiles");
  for (const k of Object.keys(TILE_LABEL) as TileKind[]) {
    const wrap = el("span", "sw");
    const input = el("input");
    input.type = "checkbox";
    wrap.append(input, el("i"));
    input.onchange = () => host.change({ tiles: { [k]: input.checked } });
    refreshers.push((p) => {
      input.checked = p.tiles[k] !== false;
      input.disabled = !p.widgets;
    });
    const r = el("label", "s-row s-tile");
    r.append(el("span", "s-label", TILE_LABEL[k]), wrap);
    grid.append(r);
  }
  fi.append(grid);
  slider(fi, "Desktop dim (cinematic)", 0, 90, 5, (p) => Math.round(p.cinematic_dim * 100), (v) => ({ cinematic_dim: v / 100 }), (v) => `${v} %`);
  toggle(fi, "Scanlines (cinematic)", (p) => p.scanlines, (v) => ({ scanlines: v }));

  // ---- actions --------------------------------------------------------------------------------
  const ac = section("ACTIONS");
  const btns = el("div", "s-actions");
  const mk = (label: string, a: Action, cls = ""): HTMLButtonElement => {
    const b = el("button", `btn ${cls}`, label);
    b.type = "button";
    b.onclick = () => host.action(a);
    btns.append(b);
    return b;
  };
  mk("Hide HUD", "hide");
  mk("Corner", "compact");
  mk("Panel", "panel");
  mk("Full interface", "cinematic");
  ac.append(btns);
  const danger = el("div", "s-actions");
  const quit = el("button", "btn danger", "Shut down J.A.R.V.I.S.");
  quit.type = "button";
  let armed: number | undefined;
  quit.onclick = () => {
    if (armed) {
      window.clearTimeout(armed);
      armed = undefined;
      host.action("shutdown");
      return;
    }
    quit.textContent = "Click again to confirm";
    quit.classList.add("armed");
    armed = window.setTimeout(() => {
      quit.textContent = "Shut down J.A.R.V.I.S.";
      quit.classList.remove("armed");
      armed = undefined;
    }, 4000);
  };
  danger.append(quit, el("small", "s-hint", "quits the assistant — not the PC. Voice: “power down”."));
  ac.append(danger);

  // ---- reference ------------------------------------------------------------------------------
  const ref = section("KEYS & VOICE");
  const keys = el("dl", "s-ref");
  const hk = host.hotkeys();
  const pairs: [string, string][] = [
    [keyName(hk.text_input), "type a command (opens the panel)"],
    [keyName(hk.toggle_hud), "hide / show the HUD"],
    ["Ctrl+Shift+M", "HUD ⇄ full interface"],
    ["Esc", "close / back to the corner"],
    ["“hide yourself”", "HUD gone, still listening"],
    ["“show the full interface”", "cinematic by voice"],
    ["“back to the corner”", "compact by voice"],
    ["“power down”", "shut down Jarvis (asks first)"],
  ];
  for (const [k, v] of pairs) {
    keys.append(el("dt", undefined, k), el("dd", undefined, v));
  }
  ref.append(keys);

  const api = {
    open(): void {
      api.refresh();
      root.classList.remove("hidden");
      document.body.classList.add("settings-open");
    },
    close(): void {
      root.classList.add("hidden");
      document.body.classList.remove("settings-open");
    },
    toggle(): void {
      api.isOpen() ? api.close() : api.open();
    },
    isOpen(): boolean {
      return !root.classList.contains("hidden");
    },
    refresh(): void {
      const p = host.prefs();
      refreshers.forEach((f) => f(p));
    },
  };
  return api;
}

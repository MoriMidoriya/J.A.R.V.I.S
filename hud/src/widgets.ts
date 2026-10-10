/** Side instruments — small canvas-2D tiles around the reactor (panel/cinematic modes), in the style of the reference:
 *  thin lines, mono labels, lots of ticks.
 *
 *   wave      AUDIO I/O   rolling level graph (Jarvis = accent, you = amber) + mirrored waveform
 *   spectrum  SPECTRUM    24 bands with falling peak caps (whichever source is live)
 *   system    SYSTEM      CPU / MEM / VRAM arc gauges, network, battery
 *   radar     TELEMETRY   pentagon radar of CPU, MEM, VRAM, NET, AUDIO
 *   clock     CHRONO      radar clock + date
 *   globe     UPLINK      wireframe globe (decoration)
 *   pipeline  PIPELINE    last turn's latencies STT / LLM / TTS / TOTAL under a ruler
 *
 *  One requestAnimationFrame loop (30 fps) draws every tile that currently has a size (display:none → skipped).
 */

import type { JarvisState, SystemStats } from "./ws";

export type Kind = "wave" | "spectrum" | "system" | "radar" | "clock" | "globe" | "pipeline";
export type Latency = { stt_ms: number | null; llm_first_token_ms: number | null; tts_first_audio_ms: number | null; total_ms: number | null };

const HIST = 180;
const N_BANDS = 24;
const MONO = '"Cascadia Code", Consolas, monospace';
const TAU = Math.PI * 2;

class Surface {
  ctx: CanvasRenderingContext2D;
  w = 0;
  h = 0;
  constructor(public el: HTMLCanvasElement) {
    this.ctx = el.getContext("2d")!;
    new ResizeObserver(() => this.fit()).observe(el);
    this.fit();
  }
  fit(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = this.el.clientWidth;
    this.h = this.el.clientHeight;
    this.el.width = Math.max(1, Math.round(this.w * dpr));
    this.el.height = Math.max(1, Math.round(this.h * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
}

function rgb(hex: string): string {
  const n = parseInt(hex.replace("#", ""), 16);
  return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

export class Instruments {
  private items: { kind: Kind; sf: Surface }[] = [];
  private histOut = new Float32Array(HIST);
  private histMic = new Float32Array(HIST);
  private head = 0;
  private curOut = 0;
  private curMic = 0;
  private bands = new Float32Array(N_BANDS);
  private shown = new Float32Array(N_BANDS);
  private peaks = new Float32Array(N_BANDS);
  private bandsAt = 0;
  private stats: SystemStats | null = null;
  private lat: Latency | null = null;
  private state: JarvisState = "booting";
  private acc = rgb("#34e4f5");
  private amber = rgb("#ffb648");
  private last = 0;
  private spots: [number, number][] = [];

  constructor(root: ParentNode = document) {
    root.querySelectorAll<HTMLCanvasElement>("canvas[data-inst]").forEach((c) => this.items.push({ kind: c.dataset.inst as Kind, sf: new Surface(c) }));
    let s = 7;
    const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 14; i++) this.spots.push([rnd() * TAU, (rnd() - 0.5) * 2.4]);
    requestAnimationFrame(this.tick);
  }

  // ---- feeds -------------------------------------------------------------------------------
  pushLevel(source: "out" | "mic", v: number): void {
    if (source === "out") this.curOut = v;
    else this.curMic = v;
  }
  setSpectrum(source: "out" | "mic", b: number[]): void {
    // whichever source is live wins; the mic is silent while Jarvis speaks and vice versa
    if (source === "mic" && this.state !== "listening" && this.state !== "confirming") return;
    for (let i = 0; i < N_BANDS; i++) this.bands[i] = b[i] ?? 0;
    this.bandsAt = performance.now();
  }
  setStats(s: SystemStats): void {
    this.stats = s;
  }
  setLatency(l: Latency): void {
    this.lat = l;
  }
  setState(s: JarvisState): void {
    this.state = s;
  }
  setAccent(hex: string): void {
    this.acc = rgb(hex);
  }

  // ---- loop --------------------------------------------------------------------------------
  private tick = (now: number): void => {
    requestAnimationFrame(this.tick);
    if (document.hidden || now - this.last < 32) return;
    this.last = now;
    const t = now / 1000;
    // sample the levels at the draw rate so both traces share one time axis
    this.histOut[this.head] = this.curOut;
    this.histMic[this.head] = this.curMic;
    this.head = (this.head + 1) % HIST;
    const stale = now - this.bandsAt > 350;
    for (let i = 0; i < N_BANDS; i++) {
      const target = stale ? 0 : this.bands[i];
      this.shown[i] += (target - this.shown[i]) * (target > this.shown[i] ? 0.6 : 0.18);
      this.peaks[i] = Math.max(this.shown[i], this.peaks[i] - 0.012);
    }
    for (const it of this.items) {
      const { sf } = it;
      // too small to be a real tile (the first frame can see a stale size from before the mode class was applied —
      // ResizeObserver delivers after rAF; the smallest laid-out tile is the panel's 118 px row)
      if (sf.w < 60 || sf.h < 60) continue;
      const ctx = sf.ctx;
      ctx.clearRect(0, 0, sf.w, sf.h);
      ctx.lineWidth = 1;
      switch (it.kind) {
        case "wave": this.wave(ctx, sf.w, sf.h); break;
        case "spectrum": this.spectrum(ctx, sf.w, sf.h); break;
        case "system": this.system(ctx, sf.w, sf.h); break;
        case "radar": this.radar(ctx, sf.w, sf.h, t); break;
        case "clock": this.clock(ctx, sf.w, sf.h); break;
        case "globe": this.globe(ctx, sf.w, sf.h, t); break;
        case "pipeline": this.pipeline(ctx, sf.w, sf.h); break;
      }
    }
  };

  // ---- helpers -----------------------------------------------------------------------------
  private text(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, size = 9, align: CanvasTextAlign = "left", color = `rgba(${this.acc},0.75)`): void {
    ctx.font = `${size}px ${MONO}`;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.fillStyle = color;
    ctx.fillText(s, x, y);
  }
  private hist(src: Float32Array, i: number): number {
    return src[(this.head + i) % HIST];
  }

  // ---- tiles -------------------------------------------------------------------------------
  private wave(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const top = 14;
    const split = top + (h - top) * 0.52;
    const gh = split - top - 6;
    ctx.strokeStyle = `rgba(${this.acc},0.12)`;
    for (const f of [0.25, 0.5, 0.75]) {
      const y = Math.round(top + gh * f) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
    const trace = (src: Float32Array, col: string, fill: boolean) => {
      ctx.beginPath();
      for (let i = 0; i < HIST; i++) {
        const x = (i / (HIST - 1)) * w;
        const y = top + gh * (1 - Math.min(1, this.hist(src, i)));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = `rgba(${col},0.9)`;
      ctx.stroke();
      if (fill) {
        ctx.lineTo(w, top + gh);
        ctx.lineTo(0, top + gh);
        ctx.closePath();
        ctx.fillStyle = `rgba(${col},0.1)`;
        ctx.fill();
      }
    };
    trace(this.histMic, this.amber, false);
    trace(this.histOut, this.acc, true);
    // mirrored waveform
    const mid = split + (h - split) / 2;
    const amp = (h - split) / 2 - 4;
    const step = 3;
    for (let x = 0; x < w; x += step) {
      const i = Math.floor((x / w) * HIST);
      const o = this.hist(this.histOut, i);
      const m = this.hist(this.histMic, i);
      const v = Math.max(o, m, 0.03);
      const hh = amp * Math.min(1, v) * (0.75 + 0.25 * Math.sin(i * 1.7));
      ctx.fillStyle = m > o ? `rgba(${this.amber},0.85)` : `rgba(${this.acc},0.85)`;
      ctx.fillRect(x, mid - hh, 1.5, hh * 2);
    }
    ctx.strokeStyle = `rgba(${this.acc},0.25)`;
    ctx.beginPath();
    ctx.moveTo(0, Math.round(mid) + 0.5);
    ctx.lineTo(w, Math.round(mid) + 0.5);
    ctx.stroke();
    this.text(ctx, "OUT", w - 4, top + 4, 8, "right");
    this.text(ctx, "MIC", w - 26, top + 4, 8, "right", `rgba(${this.amber},0.8)`);
  }

  private spectrum(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const top = 16;
    const bottom = h - 12;
    const bw = (w - 6) / N_BANDS;
    for (let i = 0; i < N_BANDS; i++) {
      const x = 3 + i * bw;
      const v = this.shown[i];
      const y = bottom - (bottom - top) * v;
      const g = ctx.createLinearGradient(0, bottom, 0, top);
      g.addColorStop(0, `rgba(${this.acc},0.35)`);
      g.addColorStop(1, `rgba(${this.acc},0.95)`);
      ctx.fillStyle = g;
      ctx.fillRect(x + 1, y, bw - 2, bottom - y);
      const py = bottom - (bottom - top) * this.peaks[i];
      ctx.fillStyle = `rgba(${this.acc},0.9)`;
      ctx.fillRect(x + 1, py - 1, bw - 2, 1.5);
      // faint ladder
      ctx.fillStyle = `rgba(${this.acc},0.08)`;
      for (let yy = bottom - 6; yy > top; yy -= 6) ctx.fillRect(x + 1, yy, bw - 2, 1);
    }
    ctx.strokeStyle = `rgba(${this.acc},0.35)`;
    ctx.beginPath();
    ctx.moveTo(0, bottom + 0.5);
    ctx.lineTo(w, bottom + 0.5);
    ctx.stroke();
    this.text(ctx, "80 Hz", 3, h - 5, 8);
    this.text(ctx, "1k", w / 2, h - 5, 8, "center");
    this.text(ctx, "8 kHz", w - 3, h - 5, 8, "right");
  }

  private gauge(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number, v: number | null, label: string, sub?: string): void {
    const a0 = Math.PI * 0.75;
    const span = Math.PI * 1.5;
    ctx.lineWidth = 3;
    ctx.lineCap = "butt";
    ctx.strokeStyle = `rgba(${this.acc},0.15)`;
    ctx.beginPath();
    ctx.arc(cx, cy, r, a0, a0 + span);
    ctx.stroke();
    if (v != null) {
      const hot = v > 0.9;
      ctx.strokeStyle = hot ? "rgba(255,90,90,0.95)" : `rgba(${this.acc},0.9)`;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a0, a0 + span * Math.min(1, Math.max(0, v)));
      ctx.stroke();
    }
    ctx.lineWidth = 1;
    for (let i = 0; i <= 10; i++) {
      const a = a0 + (span * i) / 10;
      ctx.strokeStyle = `rgba(${this.acc},0.35)`;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * (r + 5), cy + Math.sin(a) * (r + 5));
      ctx.lineTo(cx + Math.cos(a) * (r + (i % 5 === 0 ? 9 : 7)), cy + Math.sin(a) * (r + (i % 5 === 0 ? 9 : 7)));
      ctx.stroke();
    }
    this.text(ctx, v == null ? "—" : `${Math.round(v * 100)}%`, cx, cy - 1, 11, "center", `rgba(${this.acc},0.95)`);
    this.text(ctx, label, cx, cy + r + 14, 8, "center");
    if (sub) this.text(ctx, sub, cx, cy + r + 24, 8, "center", `rgba(${this.acc},0.5)`);
  }

  private system(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const s = this.stats;
    const n = 3;
    const cw = w / n;
    const r = Math.max(2, Math.min(cw / 2 - 16, (h - 52) / 2 - 10, 26));
    const cy = 16 + r + 6;
    const vram = s?.vram_used_gb != null && s.vram_total_gb ? s.vram_used_gb / s.vram_total_gb : null;
    this.gauge(ctx, cw * 0.5, cy, r, s ? s.cpu_pct / 100 : null, "CPU");
    this.gauge(ctx, cw * 1.5, cy, r, s ? s.mem_pct / 100 : null, "MEM");
    this.gauge(ctx, cw * 2.5, cy, r, vram, "VRAM", s?.vram_used_gb != null ? `${s.vram_used_gb.toFixed(1)}/${s.vram_total_gb?.toFixed(0)}G` : undefined);
    const y = cy + r + 38;
    if (y < h - 4 && s) {
      const fmt = (k: number) => (k >= 1000 ? `${(k / 1000).toFixed(1)}M` : `${Math.round(k)}k`);
      this.text(ctx, `NET ▲ ${fmt(s.net_up_kbps)}  ▼ ${fmt(s.net_down_kbps)}`, 6, y, 8);
      if (s.battery_pct != null) this.text(ctx, `BAT ${s.battery_pct}%${s.plugged ? " ⚡" : ""}`, w - 6, y, 8, "right");
    }
  }

  private radar(ctx: CanvasRenderingContext2D, w: number, h: number, t: number): void {
    const cx = w / 2;
    const cy = 14 + (h - 14) / 2;
    const R = Math.max(2, Math.min(w, h - 14) / 2 - 18);
    const s = this.stats;
    const net = s ? Math.min(1, Math.log10(1 + s.net_up_kbps + s.net_down_kbps) / 4) : 0;
    const vram = s?.vram_used_gb != null && s.vram_total_gb ? s.vram_used_gb / s.vram_total_gb : 0;
    const audio = Math.max(this.curOut, this.curMic);
    const vals = [s ? s.cpu_pct / 100 : 0, s ? s.mem_pct / 100 : 0, vram, net, audio];
    const labels = ["CPU", "MEM", "VRAM", "NET", "AUDIO"];
    const pt = (i: number, f: number) => {
      const a = -Math.PI / 2 + (i / 5) * TAU;
      return [cx + Math.cos(a) * R * f, cy + Math.sin(a) * R * f] as const;
    };
    for (const f of [0.25, 0.5, 0.75, 1]) {
      ctx.strokeStyle = `rgba(${this.acc},${f === 1 ? 0.45 : 0.15})`;
      ctx.beginPath();
      for (let i = 0; i <= 5; i++) {
        const [x, y] = pt(i % 5, f);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    ctx.strokeStyle = `rgba(${this.acc},0.2)`;
    for (let i = 0; i < 5; i++) {
      const [x, y] = pt(i, 1);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x, y);
      ctx.stroke();
      const [lx, ly] = pt(i, 1.22);
      this.text(ctx, labels[i], lx, ly, 8, "center");
    }
    ctx.beginPath();
    for (let i = 0; i <= 5; i++) {
      const [x, y] = pt(i % 5, 0.08 + 0.92 * Math.min(1, vals[i % 5]));
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.fillStyle = `rgba(${this.acc},0.18)`;
    ctx.fill();
    ctx.strokeStyle = `rgba(${this.acc},0.9)`;
    ctx.stroke();
    for (let i = 0; i < 5; i++) {
      const [x, y] = pt(i, 0.08 + 0.92 * Math.min(1, vals[i]));
      ctx.fillStyle = `rgba(${this.acc},1)`;
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, TAU);
      ctx.fill();
    }
    // sweep
    const a = (t * 0.8) % TAU;
    const g = ctx.createConicGradient(a, cx, cy);
    g.addColorStop(0, `rgba(${this.acc},0.25)`);
    g.addColorStop(0.25, `rgba(${this.acc},0)`);
    g.addColorStop(1, `rgba(${this.acc},0)`);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, TAU);
    ctx.fill();
  }

  private clock(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const d = new Date();
    const cx = w / 2;
    const R = Math.max(2, Math.min(w / 2, (h - 36) / 2) - 8);
    const cy = 14 + R + 4;
    const sec = d.getSeconds() + d.getMilliseconds() / 1000;
    const min = d.getMinutes() + sec / 60;
    const hr = (d.getHours() % 12) + min / 60;
    for (let i = 0; i < 60; i++) {
      const a = (i / 60) * TAU;
      const long = i % 5 === 0;
      ctx.strokeStyle = `rgba(${this.acc},${long ? 0.8 : 0.3})`;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * (R - (long ? 7 : 4)), cy + Math.sin(a) * (R - (long ? 7 : 4)));
      ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
      ctx.stroke();
    }
    ctx.strokeStyle = `rgba(${this.acc},0.35)`;
    ctx.beginPath();
    ctx.arc(cx, cy, R - 12, 0, TAU);
    ctx.stroke();
    const sa = -Math.PI / 2 + (sec / 60) * TAU;
    const g = ctx.createConicGradient(sa - Math.PI * 0.6, cx, cy);
    g.addColorStop(0, `rgba(${this.acc},0)`);
    g.addColorStop(0.3, `rgba(${this.acc},0.28)`);
    g.addColorStop(0.3001, `rgba(${this.acc},0)`);
    g.addColorStop(1, `rgba(${this.acc},0)`);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, R - 13, 0, TAU);
    ctx.fill();
    const hand = (ang: number, len: number, width: number, alpha: number) => {
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.strokeStyle = `rgba(${this.acc},${alpha})`;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(ang) * len, cy + Math.sin(ang) * len);
      ctx.stroke();
    };
    hand(-Math.PI / 2 + (hr / 12) * TAU, R * 0.5, 2.5, 0.95);
    hand(-Math.PI / 2 + (min / 60) * TAU, R * 0.72, 1.5, 0.85);
    hand(sa, R - 14, 1, 0.9);
    ctx.lineWidth = 1;
    ctx.fillStyle = `rgba(${this.acc},1)`;
    ctx.beginPath();
    ctx.arc(cx, cy, 2.5, 0, TAU);
    ctx.fill();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    this.text(ctx, `${hh}:${mm}:${ss}`, cx, cy + R + 14, 11, "center", `rgba(${this.acc},0.95)`);
    this.text(ctx, d.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" }).toUpperCase(), cx, cy + R + 26, 8, "center");
  }

  private globe(ctx: CanvasRenderingContext2D, w: number, h: number, t: number): void {
    const cx = w / 2;
    const cy = 14 + (h - 14) / 2;
    const R = Math.max(2, Math.min(w, h - 14) / 2 - 10);
    const rot = t * 0.22;
    const tilt = 0.4;
    const ct = Math.cos(tilt);
    const stl = Math.sin(tilt);
    const proj = (lon: number, lat: number): [number, number, number] => {
      const x = Math.cos(lat) * Math.sin(lon + rot);
      const y = Math.sin(lat);
      const z = Math.cos(lat) * Math.cos(lon + rot);
      return [cx + R * x, cy - R * (y * ct - z * stl), y * stl + z * ct];
    };
    const curve = (pts: [number, number, number][], front: boolean) => {
      ctx.strokeStyle = `rgba(${this.acc},${front ? 0.6 : 0.12})`;
      ctx.beginPath();
      let pen = false;
      for (const [x, y, z] of pts) {
        if (z > 0 === front) {
          if (pen) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
          pen = true;
        } else pen = false;
      }
      ctx.stroke();
    };
    const meridians: [number, number, number][][] = [];
    for (let m = 0; m < 12; m++) {
      const pts: [number, number, number][] = [];
      for (let i = 0; i <= 48; i++) pts.push(proj((m / 12) * Math.PI * 2, -Math.PI / 2 + (i / 48) * Math.PI));
      meridians.push(pts);
    }
    const parallels: [number, number, number][][] = [];
    for (let p = -60; p <= 60; p += 30) {
      const pts: [number, number, number][] = [];
      for (let i = 0; i <= 72; i++) pts.push(proj((i / 72) * TAU, (p / 180) * Math.PI));
      parallels.push(pts);
    }
    for (const back of [false, true]) {
      for (const m of meridians) curve(m, back);
      for (const p of parallels) curve(p, back);
    }
    ctx.strokeStyle = `rgba(${this.acc},0.8)`;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, TAU);
    ctx.stroke();
    for (const [lon, lat] of this.spots) {
      const [x, y, z] = proj(lon, lat);
      if (z <= 0.05) continue;
      ctx.fillStyle = `rgba(${this.acc},${0.5 + 0.5 * z})`;
      ctx.beginPath();
      ctx.arc(x, y, 1.6 + z, 0, TAU);
      ctx.fill();
    }
    // orbit ring
    ctx.strokeStyle = `rgba(${this.acc},0.3)`;
    ctx.beginPath();
    ctx.ellipse(cx, cy, R + 7, (R + 7) * 0.32, -0.35, 0, TAU);
    ctx.stroke();
    const oa = t * 1.1;
    const ox = cx + Math.cos(oa) * (R + 7);
    const oy = cy + Math.sin(oa) * (R + 7) * 0.32;
    const rx = Math.cos(-0.35) * (ox - cx) - Math.sin(-0.35) * (oy - cy) + cx;
    const ry = Math.sin(-0.35) * (ox - cx) + Math.cos(-0.35) * (oy - cy) + cy;
    ctx.fillStyle = `rgba(${this.acc},1)`;
    ctx.beginPath();
    ctx.arc(rx, ry, 2, 0, TAU);
    ctx.fill();
  }

  private pipeline(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    // ruler along the top edge
    ctx.strokeStyle = `rgba(${this.acc},0.5)`;
    ctx.beginPath();
    ctx.moveTo(0, 0.5);
    ctx.lineTo(w, 0.5);
    ctx.stroke();
    for (let x = 0, i = 0; x <= w; x += 8, i++) {
      const len = i % 10 === 0 ? 9 : i % 5 === 0 ? 6 : 3;
      ctx.strokeStyle = `rgba(${this.acc},${len > 3 ? 0.6 : 0.3})`;
      ctx.beginPath();
      ctx.moveTo(x + 0.5, 1);
      ctx.lineTo(x + 0.5, 1 + len);
      ctx.stroke();
    }
    const l = this.lat;
    const top = 22;
    if (!l) {
      this.text(ctx, "AWAITING FIRST TURN", w / 2, top + (h - top) / 2, 8, "center", `rgba(${this.acc},0.35)`);
      return;
    }
    const rows: [string, number | null][] = [["STT", l.stt_ms], ["LLM", l.llm_first_token_ms], ["TTS", l.tts_first_audio_ms], ["TOTAL", l.total_ms]];
    const rowH = Math.min(14, (h - top - 2) / rows.length);
    const x0 = 44;
    const x1 = w - 58;
    const scale = Math.max(1500, l.total_ms ?? 0);
    rows.forEach(([name, v], i) => {
      const y = top + i * rowH + rowH / 2;
      this.text(ctx, name, 0, y, 8);
      ctx.fillStyle = `rgba(${this.acc},0.1)`;
      ctx.fillRect(x0, y - 2, x1 - x0, 4);
      if (v != null) {
        ctx.fillStyle = name === "TOTAL" ? `rgba(${this.acc},0.95)` : `rgba(${this.acc},0.6)`;
        ctx.fillRect(x0, y - 2, (x1 - x0) * Math.min(1, v / scale), 4);
        this.text(ctx, `${v} ms`, w, y, 8, "right", `rgba(${this.acc},0.9)`);
      } else this.text(ctx, "—", w, y, 8, "right", `rgba(${this.acc},0.4)`);
    });
  }
}

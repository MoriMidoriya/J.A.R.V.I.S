/** Three.js reactor scene — a stack of concentric instrument rings, read from the centre outwards:
 *
 *   core        animated plasma disc + white-hot centre dot + soft halo        (light pulse: mild, see below)
 *   sweep       radar wedge inside the inner ring (slow when idle, fast when listening/thinking)
 *   blocks      36 thick dashes, slow random flicker
 *   fine ticks  144 hairline ticks, counter-rotating
 *   data ring   160 tiny dashes that blink like a data stream (fast while thinking)
 *   gap ring    3 arcs with gaps
 *   BARS        96 radial bars whose LENGTH follows the spectrum of Jarvis' voice (24 bands, mirrored left/right,
 *               low frequencies at the bottom) — this is what "pulses" now, instead of the whole reactor flashing.
 *               While listening the same bars follow the microphone; while thinking two pulses chase around the ring.
 *   ruler       outer ring with 90 ticks
 *   brackets    4 thick arcs with glowing end caps (+ 4 thin ones counter-rotating) that expand ~5 % with the voice
 *   satellites  3 bright dots orbiting the ruler
 *
 *  Light level still pulses, but gently: bloom 0.33→0.55, halo 0.10→0.32, core brightness ×1→×1.6. Everything is
 *  additive glow on black, so the final ShaderPass turns brightness into alpha (transparent window, see D17).
 *
 *  Public API: setState(), setSpeechLevel(), setMicLevel(), setSpectrum(), setVisible(), pulse(); resize is automatic.
 */

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import type { JarvisState } from "./ws";

// ------------------------------------------------------------------------------------------------ shaders
/** UnrealBloomPass writes alpha = 1 everywhere; derive alpha from brightness instead (glow on black = coverage).
 *  The lower threshold (0.075, measured after the sRGB OutputPass) cuts the faint bloom haze that otherwise tints the
 *  whole canvas and shows up as a ghost square over the desktop. */
const ALPHA_FROM_LUMA = {
  uniforms: { tDiffuse: { value: null as THREE.Texture | null } },
  vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse; varying vec2 vUv;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      float m = max(c.r, max(c.g, c.b));
      float a = clamp(m, 0.0, 1.0) * smoothstep(0.075, 0.2, m);
      gl_FragColor = vec4(m > 0.002 ? min(c.rgb / m, vec3(1.0)) : vec3(0.0), a);
    }`,
};

const NOISE_GLSL = /* glsl */ `
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }`;

/** Plasma core: radial glow + slowly turning noise filaments; brightness follows uLevel (gently). */
const CORE_FRAG = /* glsl */ `
  uniform vec3 uColor; uniform float uTime; uniform float uLevel; varying vec2 vUv;
  ${NOISE_GLSL}
  void main() {
    vec2 p = vUv - 0.5;
    float d = length(p) * 2.0;
    if (d > 1.0) discard;
    float ang = atan(p.y, p.x);
    float n = noise(vec3(p * 5.0, uTime * 0.35));
    float fil = pow(0.5 + 0.5 * sin(ang * 4.0 + uTime * 0.9 + n * 6.0), 3.0);
    float body = smoothstep(1.0, 0.0, d);
    float glow = pow(body, 2.4) * (0.55 + 0.5 * uLevel);
    float ring = smoothstep(0.06, 0.0, abs(d - 0.78)) * 0.35;
    float a = glow + body * (0.18 + 0.25 * uLevel) * fil * n + ring;
    vec3 c = mix(uColor, vec3(1.0), smoothstep(0.55, 1.0, body) * 0.55);
    gl_FragColor = vec4(c * a * (1.0 + 0.6 * uLevel), a);
  }`;

const FLAT_VERT = /* glsl */ `varying vec2 vUv; varying vec3 vPos; void main() { vUv = uv; vPos = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

/** Dash rings: each quad carries a seed; dashes blink on/off in time slots (rate in Hz) — the "data stream" look. */
const DASH_VERT = /* glsl */ `attribute float aSeed; varying float vSeed; void main() { vSeed = aSeed; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const DASH_FRAG = /* glsl */ `
  uniform vec3 uColor; uniform float uTime; uniform float uOpacity; uniform float uRate; uniform float uBase; varying float vSeed;
  float h(float n) { return fract(sin(n) * 43758.5453); }
  void main() {
    float slot = floor(uTime * uRate + vSeed * 9.0);
    float on = step(0.5, h(vSeed * 131.7 + slot * 0.37));
    gl_FragColor = vec4(uColor, uOpacity * mix(uBase, 1.0, on));
  }`;

/** Radar sweep wedge: alpha rises towards the leading edge. */
const SWEEP_FRAG = /* glsl */ `
  uniform vec3 uColor; uniform float uOpacity; uniform float uSpan; varying vec3 vPos;
  void main() {
    float a = atan(vPos.y, vPos.x); if (a < 0.0) a += 6.2831853;
    float t = clamp(a / uSpan, 0.0, 1.0);
    float r = length(vPos.xy);
    gl_FragColor = vec4(uColor, uOpacity * pow(t, 3.0) * smoothstep(0.05, 0.25, r));
  }`;

/** Instanced spectrum bars: brighter towards the tip, with a hot cap. */
const BAR_VERT = /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0); }`;
const BAR_FRAG = /* glsl */ `
  uniform vec3 uColor; uniform float uOpacity; varying vec2 vUv;
  void main() {
    float a = 0.45 + 0.55 * vUv.y + 1.2 * smoothstep(0.86, 1.0, vUv.y);
    gl_FragColor = vec4(uColor * (1.0 + 0.5 * smoothstep(0.86, 1.0, vUv.y)), uOpacity * a);
  }`;

// ------------------------------------------------------------------------------------------------ constants
const STATE_COLOR: Record<JarvisState, number> = {
  booting: 0x2a86a8,
  idle: 0x34e4f5,
  listening: 0xffb648,
  thinking: 0xc6ecff,
  speaking: 0x34e4f5,
  confirming: 0xffb648,
  error: 0xff5a5a,
};
const N_BARS = 96;
const N_BANDS = 24;
const BAR_R0 = 1.29;
const BAR_MIN = 0.05;
const BAR_MAX = 0.44;
const TAU = Math.PI * 2;
const ADD = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide } as const;

type Spin = { obj: THREE.Object3D; speed: number };

function smooth(cur: number, target: number, attack: number, release: number): number {
  return cur + (target - cur) * (target > cur ? attack : release);
}

// ------------------------------------------------------------------------------------------------ geometry helpers
function arcMesh(rIn: number, rOut: number, start: number, len: number, opacity: number, mult = 1): THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial> {
  const m = new THREE.Mesh(new THREE.RingGeometry(rIn, rOut, Math.max(8, Math.round(len * 40)), 1, start, len), new THREE.MeshBasicMaterial({ ...ADD, opacity }));
  m.userData.mult = mult;
  return m;
}

function lineRing(r: number, opacity: number, segs = 160): THREE.LineLoop {
  const pts: number[] = [];
  for (let i = 0; i < segs; i++) pts.push(Math.cos((i / segs) * TAU) * r, Math.sin((i / segs) * TAU) * r, 0);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return new THREE.LineLoop(g, new THREE.LineBasicMaterial({ transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false }));
}

function tickRing(r0: number, r1: number, n: number, longEvery: number, r1Long: number, opacity: number): THREE.LineSegments {
  const pts: number[] = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * TAU;
    const r = i % longEvery === 0 ? r1Long : r1;
    pts.push(Math.cos(a) * r0, Math.sin(a) * r0, 0, Math.cos(a) * r, Math.sin(a) * r, 0);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false }));
}

/** n dashes between r0..r1, each covering `fill` of its angular pitch; per-dash seed for the flicker shader. */
function dashRing(r0: number, r1: number, n: number, fill: number, opacity: number, rate: number, base: number): THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial> {
  const pos: number[] = [];
  const seed: number[] = [];
  const idx: number[] = [];
  const pitch = TAU / n;
  for (let i = 0; i < n; i++) {
    const a0 = i * pitch;
    const a1 = a0 + pitch * fill;
    const s = Math.random();
    const base4 = i * 4;
    pos.push(Math.cos(a0) * r0, Math.sin(a0) * r0, 0, Math.cos(a1) * r0, Math.sin(a1) * r0, 0, Math.cos(a1) * r1, Math.sin(a1) * r1, 0, Math.cos(a0) * r1, Math.sin(a0) * r1, 0);
    seed.push(s, s, s, s);
    idx.push(base4, base4 + 1, base4 + 2, base4, base4 + 2, base4 + 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("aSeed", new THREE.Float32BufferAttribute(seed, 1));
  g.setIndex(idx);
  const mat = new THREE.ShaderMaterial({
    vertexShader: DASH_VERT,
    fragmentShader: DASH_FRAG,
    uniforms: { uColor: { value: new THREE.Color(1, 1, 1) }, uTime: { value: 0 }, uOpacity: { value: opacity }, uRate: { value: rate }, uBase: { value: base } },
    ...ADD,
  });
  return new THREE.Mesh(g, mat);
}

function dot(r: number, mult: number, opacity = 1): THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial> {
  const m = new THREE.Mesh(new THREE.CircleGeometry(r, 20), new THREE.MeshBasicMaterial({ ...ADD, opacity }));
  m.userData.mult = mult;
  return m;
}

function haloTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(255,255,255,0.35)");
  g.addColorStop(0.6, "rgba(255,255,255,0.06)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

// ------------------------------------------------------------------------------------------------ scene
export class ReactorScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private clock = new THREE.Clock();

  private root = new THREE.Group();
  private spins: Spin[] = [];
  private basics: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>[] = [];
  private lines: (THREE.LineLoop | THREE.LineSegments)[] = [];
  private dashes: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>[] = [];
  private coreMat: THREE.ShaderMaterial;
  private sweepMat: THREE.ShaderMaterial;
  private sweep: THREE.Mesh;
  private halo: THREE.Sprite;
  private brackets = new THREE.Group();
  private bars: THREE.InstancedMesh;
  private barMat: THREE.ShaderMaterial;
  private barLen = new Float32Array(N_BARS).fill(BAR_MIN);
  private barSeed = new Float32Array(N_BARS).map(() => Math.random());
  private satellites: { obj: THREE.Mesh; r: number; speed: number; phase: number }[] = [];

  private color = new THREE.Color(STATE_COLOR.booting);
  private targetColor = new THREE.Color(STATE_COLOR.booting);
  private state: JarvisState = "booting";
  private speech = 0;
  private speechTarget = 0;
  private mic = 0;
  private micTarget = 0;
  private bandsOut = new Float32Array(N_BANDS);
  private bandsMic = new Float32Array(N_BANDS);
  private lastSpectrum = { out: 0, mic: 0 };
  private visible = true;
  private raf = 0;
  private spin = 0;
  private flash = 0;
  private mat4 = new THREE.Matrix4();
  private quat = new THREE.Quaternion();
  private vec = new THREE.Vector3();
  private scl = new THREE.Vector3();
  private zAxis = new THREE.Vector3(0, 0, 1);

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, premultipliedAlpha: false, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.1, 50);
    this.camera.position.set(0, 0, 8);
    this.scene.add(this.root);

    // --- halo + core + centre dot
    this.halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTexture(), transparent: true, opacity: 0.14, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.halo.scale.setScalar(2.6);
    this.root.add(this.halo);
    this.coreMat = new THREE.ShaderMaterial({ vertexShader: FLAT_VERT, fragmentShader: CORE_FRAG, uniforms: { uColor: { value: new THREE.Color() }, uTime: { value: 0 }, uLevel: { value: 0 } }, ...ADD });
    this.root.add(new THREE.Mesh(new THREE.PlaneGeometry(0.84, 0.84), this.coreMat));
    const centre = dot(0.05, 2.6);
    this.basics.push(centre);
    this.root.add(centre);

    // --- inner ring + radar sweep
    this.addLine(lineRing(0.47, 0.7), 0);
    this.sweepMat = new THREE.ShaderMaterial({ vertexShader: FLAT_VERT, fragmentShader: SWEEP_FRAG, uniforms: { uColor: { value: new THREE.Color() }, uOpacity: { value: 0.4 }, uSpan: { value: 1.1 } }, ...ADD });
    this.sweep = new THREE.Mesh(new THREE.RingGeometry(0.06, 0.46, 48, 1, 0, 1.1), this.sweepMat);
    this.root.add(this.sweep);

    // --- block ring, fine ticks, data ring, gap ring, bar track
    this.addDash(dashRing(0.56, 0.7, 36, 0.62, 0.55, 0.7, 0.45), 0.1);
    this.addLine(tickRing(0.78, 0.84, 144, 12, 0.89, 0.5), -0.06);
    this.addLine(lineRing(0.95, 0.3), 0.03);
    this.addDash(dashRing(1.0, 1.08, 160, 0.5, 0.75, 2.5, 0.12), 0.18);
    const gaps = new THREE.Group();
    for (let i = 0; i < 3; i++) gaps.add(this.addBasic(arcMesh(1.16, 1.175, (i / 3) * TAU, TAU / 3 - 0.35, 0.8, 1.1)));
    this.addSpin(gaps, -0.12);
    this.addLine(lineRing(BAR_R0 - 0.02, 0.28), 0);

    // --- spectrum bars
    const barGeo = new THREE.PlaneGeometry(1, 1);
    barGeo.translate(0, 0.5, 0); // anchor at the inner end so scale.y extends outward
    this.barMat = new THREE.ShaderMaterial({ vertexShader: BAR_VERT, fragmentShader: BAR_FRAG, uniforms: { uColor: { value: new THREE.Color() }, uOpacity: { value: 0.75 } }, ...ADD });
    this.bars = new THREE.InstancedMesh(barGeo, this.barMat, N_BARS);
    this.bars.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.root.add(this.bars);

    // --- ruler
    this.addLine(lineRing(1.86, 0.45), 0.02);
    this.addLine(tickRing(1.86, 1.905, 90, 15, 1.955, 0.6), 0.02);

    // --- brackets (thick arcs with hot caps) and thin counter-rotating arcs
    for (let i = 0; i < 4; i++) {
      const start = (i / 4) * TAU + 0.35;
      const len = 1.0;
      this.brackets.add(this.addBasic(arcMesh(2.05, 2.12, start, len, 0.85, 1.15)));
      for (const a of [start, start + len]) {
        const cap = dot(0.04, 2.2);
        cap.position.set(Math.cos(a) * 2.085, Math.sin(a) * 2.085, 0);
        this.brackets.add(this.addBasic(cap));
      }
    }
    this.root.add(this.brackets);
    this.spins.push({ obj: this.brackets, speed: 0.08 });
    const thin = new THREE.Group();
    for (let i = 0; i < 4; i++) thin.add(this.addBasic(arcMesh(2.2, 2.214, (i / 4) * TAU + 1.9, 0.42, 0.6)));
    this.addSpin(thin, -0.2);

    // --- satellites
    for (const [speed, phase] of [[0.5, 0], [-0.33, 2.1], [0.21, 4.0]] as const) {
      const d = dot(0.028, 2.0);
      this.root.add(d);
      this.basics.push(d);
      this.satellites.push({ obj: d, r: 1.955, speed, phase });
    }

    // --- post
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.35, 0.5, 0.28);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
    this.composer.addPass(new ShaderPass(ALPHA_FROM_LUMA));

    new ResizeObserver(() => this.resize()).observe(canvas.parentElement ?? document.body);
    this.resize();
    document.addEventListener("visibilitychange", () => this.setVisible(!document.hidden));
    this.loop();
  }

  private addBasic<T extends THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>>(m: T): T {
    this.basics.push(m);
    return m;
  }
  private addLine(l: THREE.LineLoop | THREE.LineSegments, speed: number): void {
    this.lines.push(l);
    this.root.add(l);
    if (speed) this.spins.push({ obj: l, speed });
  }
  private addDash(d: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>, speed: number): void {
    this.dashes.push(d);
    this.root.add(d);
    this.spins.push({ obj: d, speed });
  }
  private addSpin(g: THREE.Group, speed: number): void {
    this.root.add(g);
    this.spins.push({ obj: g, speed });
  }

  // ---- public API --------------------------------------------------------------------------
  setState(s: JarvisState): void {
    this.state = s;
    this.targetColor.setHex(STATE_COLOR[s] ?? STATE_COLOR.idle);
  }
  /** RMS of Jarvis' voice 0..1 (~40 Hz). Drives the *mild* light pulse; the bars use the spectrum. */
  setSpeechLevel(v: number): void {
    this.speechTarget = Math.min(1, Math.max(0, v) * 1.1);
  }
  /** Mic RMS 0..1 while listening (~12 Hz). */
  setMicLevel(v: number): void {
    this.micTarget = Math.min(1, Math.max(0, v) * 2.2);
  }
  /** 24 bands 0..1 from the backend (out: 25 Hz, mic: ~12 Hz). */
  setSpectrum(source: "out" | "mic", bands: number[]): void {
    const dst = source === "out" ? this.bandsOut : this.bandsMic;
    for (let i = 0; i < N_BANDS; i++) dst[i] = bands[i] ?? 0;
    this.lastSpectrum[source] = performance.now();
  }
  setVisible(v: boolean): void {
    if (v === this.visible) return;
    this.visible = v;
    if (v) {
      this.clock.getDelta();
      this.loop();
    } else cancelAnimationFrame(this.raf);
  }
  /** Wake flash: brackets kick outwards and the core blinks once. */
  pulse(): void {
    this.flash = 1;
  }

  // ---- internals ---------------------------------------------------------------------------
  private resize(): void {
    const el = this.canvas.parentElement ?? document.body;
    const w = Math.max(1, el.clientWidth);
    const h = Math.max(1, el.clientHeight);
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    const fit = 2.72; // outermost arc 2.214 + caps + room for the bloom to fade before the canvas edge (else a visible square)
    const vfov = (this.camera.fov * Math.PI) / 180;
    this.camera.position.z = Math.max(fit / Math.tan(vfov / 2), fit / (Math.tan(vfov / 2) * this.camera.aspect));
    this.camera.updateProjectionMatrix();
  }

  /** Band value at position s∈[0,1] along one mirrored half (0 = bottom/low, 1 = top/high), linear interpolation. */
  private bandAt(src: Float32Array, s: number): number {
    const x = s * (N_BANDS - 1);
    const i = Math.min(N_BANDS - 2, Math.floor(x));
    const f = x - i;
    return src[i] * (1 - f) + src[i + 1] * f;
  }

  private loop = (): void => {
    if (!this.visible) return;
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;
    const st = this.state;
    const listening = st === "listening" || st === "confirming";
    const thinking = st === "thinking";
    const speaking = st === "speaking";
    const now = performance.now();

    // smoothing: fast attack, slow release
    this.speech = smooth(this.speech, speaking ? this.speechTarget : 0, 0.5, 0.1);
    this.mic = smooth(this.mic, listening ? this.micTarget : 0, 0.5, 0.12);
    this.flash = Math.max(0, this.flash - dt * 1.6);
    this.color.lerp(this.targetColor, 0.07);

    // one scalar "energy" for the gentle light pulse
    const energy = Math.min(1, Math.max(this.speech, this.mic * 0.6, thinking ? 0.2 + 0.1 * Math.sin(t * 6) : 0) + this.flash * 0.8);
    const speedMul = (st === "error" ? 0.15 : st === "booting" ? 0.5 : listening ? 1.7 : thinking ? 3.4 : speaking ? 1.25 : 1) * (1 + this.flash * 2);

    // rotations
    this.spin += dt * speedMul;
    for (const s of this.spins) s.obj.rotation.z += dt * s.speed * speedMul;
    this.sweep.rotation.z -= dt * (st === "error" ? 0 : listening ? 3.2 : thinking ? 6 : speaking ? 1.6 : 0.9);
    for (const s of this.satellites) {
      const a = s.phase + t * s.speed * (thinking ? 2.5 : 1);
      s.obj.position.set(Math.cos(a) * s.r, Math.sin(a) * s.r, 0);
    }

    // colours
    const c = this.color;
    for (const m of this.basics) m.material.color.copy(c).multiplyScalar((m.userData.mult as number) ?? 1);
    for (const l of this.lines) (l.material as THREE.LineBasicMaterial).color.copy(c);
    for (const d of this.dashes) {
      d.material.uniforms.uColor.value.copy(c);
      d.material.uniforms.uTime.value = t;
    }
    this.dashes[1].material.uniforms.uRate.value = thinking ? 9 : speaking ? 4 : 2.5;
    this.coreMat.uniforms.uColor.value.copy(c);
    this.coreMat.uniforms.uTime.value = t;
    this.coreMat.uniforms.uLevel.value = energy;
    this.sweepMat.uniforms.uColor.value.copy(c);
    this.sweepMat.uniforms.uOpacity.value = listening ? 0.6 : thinking ? 0.5 : 0.32;
    (this.halo.material as THREE.SpriteMaterial).color.copy(c);
    (this.halo.material as THREE.SpriteMaterial).opacity = 0.1 + 0.22 * energy;
    this.halo.scale.setScalar(2.6 + 0.5 * energy);
    this.barMat.uniforms.uColor.value.copy(c);
    this.barMat.uniforms.uOpacity.value = 0.6 + 0.3 * Math.max(this.speech, this.mic);

    // brackets breathe (not flash) with the voice
    this.brackets.scale.setScalar(1 + 0.05 * energy + (listening ? 0.04 : 0) + this.flash * 0.08);

    // --- bars: length per bar from the spectrum (speaking / listening) or a state animation
    const outFresh = now - this.lastSpectrum.out < 400;
    const micFresh = now - this.lastSpectrum.mic < 600;
    for (let i = 0; i < N_BARS; i++) {
      const u = i / N_BARS;
      const ang = -Math.PI / 2 + u * TAU;
      const s = u < 0.5 ? u * 2 : (1 - u) * 2; // mirrored halves, low at the bottom
      const seed = this.barSeed[i];
      let target: number;
      if (speaking && outFresh) {
        const b = this.bandAt(this.bandsOut, s);
        target = BAR_MIN + BAR_MAX * b * (0.9 + 0.1 * Math.sin(t * 9 + seed * TAU));
      } else if (listening && micFresh) {
        const b = Math.max(0, this.bandAt(this.bandsMic, s) - 0.1) * 1.25;
        target = BAR_MIN + BAR_MAX * 0.8 * b + 0.015 * Math.sin(t * 5 + i * 0.4);
      } else if (listening) {
        target = BAR_MIN + 0.3 * this.mic * (0.5 + 0.5 * Math.sin(t * 7 + seed * TAU)) + 0.012 * Math.sin(t * 5 + i * 0.4);
      } else if (thinking) {
        const p1 = Math.pow(Math.max(0, Math.sin(ang * 2 + t * 5.5)), 6);
        const p2 = Math.pow(Math.max(0, Math.sin(-ang * 3 + t * 7.3)), 10);
        target = 0.06 + 0.3 * p1 + 0.14 * p2 + 0.02 * seed;
      } else if (st === "error") {
        target = 0.03;
      } else if (st === "booting") {
        target = BAR_MIN + 0.12 * Math.pow(Math.max(0, Math.sin(ang - t * 2.5)), 4);
      } else {
        // idle: a slow wave travels around the ring so it reads as alive without flashing
        target = BAR_MIN + 0.05 * (0.5 + 0.5 * Math.sin(t * 1.4 - ang * 3)) + this.flash * 0.25 * seed;
      }
      this.barLen[i] = smooth(this.barLen[i], target, 0.55, 0.11);
      this.quat.setFromAxisAngle(this.zAxis, ang - Math.PI / 2);
      this.vec.set(Math.cos(ang) * BAR_R0, Math.sin(ang) * BAR_R0, 0);
      this.scl.set(0.02, this.barLen[i], 1);
      this.mat4.compose(this.vec, this.quat, this.scl);
      this.bars.setMatrixAt(i, this.mat4);
    }
    this.bars.instanceMatrix.needsUpdate = true;

    // light level: mild
    this.bloom.strength = 0.33 + 0.22 * energy;
    this.composer.render();
  };
}

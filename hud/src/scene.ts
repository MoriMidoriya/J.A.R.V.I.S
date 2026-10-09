/** Three.js arc-reactor scene.
 *
 *  Layers (back to front)
 *   - particle sphere  : ~6k points on a sphere; a vertex shader displaces each point along its normal by
 *                        noise(t) * (idleBreath + speechLevel). This is what "pulsates with Jarvis' voice".
 *   - reactor rings    : 3 concentric torus rings rotating at different speeds, plus tick marks; colour = state.
 *   - core             : glowing disc that brightens with the speech level.
 *   - waveform ring    : 128 bars around the core, heights follow the *mic* level while listening.
 *   - bloom            : UnrealBloomPass on the whole frame for the neon glow.
 *
 *  Public API: setState(), setSpeechLevel(), setMicLevel(), setVisible(), resize via ResizeObserver.
 *  All levels are smoothed (attack fast, release slow) so 20 Hz level events feel continuous at 60 fps.
 */

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import type { JarvisState } from "./ws";

/** UnrealBloomPass's blur writes alpha = 1 everywhere, which makes the whole canvas opaque black and defeats the
 *  transparent window. Everything in this scene is glow on black, so treat brightness as coverage:
 *  alpha = max(r,g,b) and un-premultiply the colour → composites over the desktop like additive light. */
const ALPHA_FROM_LUMA = {
  uniforms: { tDiffuse: { value: null as THREE.Texture | null } },
  vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse; varying vec2 vUv;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      float m = max(c.r, max(c.g, c.b));
      // the widest bloom mip leaves a ~2% haze over the whole frame; fade that out so the window stays clear
      float a = clamp(m, 0.0, 1.0) * smoothstep(0.02, 0.14, m);
      gl_FragColor = vec4(m > 0.002 ? min(c.rgb / m, vec3(1.0)) : vec3(0.0), a);
    }`,
};

const STATE_COLOR: Record<JarvisState, number> = {
  booting: 0x2a7fa0,
  idle: 0x38e8ff,
  listening: 0xffb648,
  thinking: 0xbfe9ff,
  speaking: 0x38e8ff,
  confirming: 0xffb648,
  error: 0xff5a5a,
};

const PARTICLE_VERT = /* glsl */ `
  uniform float uTime;
  uniform float uLevel;      // speech level 0..1 (smoothed)
  uniform float uBreath;     // idle breathing amplitude
  uniform float uSize;
  attribute float aSeed;
  varying float vGlow;

  // cheap 3D hash noise
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }

  void main() {
    vec3 n = normalize(position);
    float slow = noise(n * 2.0 + uTime * 0.25) - 0.5;          // gentle drifting lumps
    float fast = noise(n * 6.0 + uTime * 2.5 + aSeed) - 0.5;   // fine speech ripple
    float breath = sin(uTime * 1.3 + aSeed * 6.283) * 0.5 + 0.5;
    // At full level the sphere grows ~0.5 (radius 1.15 -> ~1.65), staying inside the inner ring (1.55-1.75).
    float disp = uBreath * (0.6 * slow + 0.08 * breath) + uLevel * (0.18 + 0.5 * fast + 0.2 * slow);
    vec3 p = position + n * disp;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    float depth = clamp((-mv.z - 2.0) / 3.0, 0.0, 1.0);
    gl_PointSize = uSize * (1.0 + uLevel * 0.3) * (1.5 - depth) * (300.0 / -mv.z);
    vGlow = 0.15 + 0.45 * (1.0 - depth) + uLevel * 0.25;
  }
`;

const PARTICLE_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uAlpha;      // scaled with canvas size: 6000 additive points saturate a small canvas
  varying float vGlow;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float a = smoothstep(0.5, 0.1, d);
    gl_FragColor = vec4(uColor * vGlow, a * uAlpha);
  }
`;

function smooth(cur: number, target: number, attack: number, release: number): number {
  const k = target > cur ? attack : release;
  return cur + (target - cur) * k;
}

export class ReactorScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private clock = new THREE.Clock();

  private particles: THREE.Points;
  private particleMat: THREE.ShaderMaterial;
  private rings: THREE.Mesh[] = [];
  private ticks: THREE.LineSegments;
  private core: THREE.Mesh;
  private coreMat: THREE.MeshBasicMaterial;
  private wave: THREE.LineSegments;
  private waveGeo: THREE.BufferGeometry;
  private waveBars = 128;
  private waveHeights = new Float32Array(128);
  private micHistory = new Float32Array(128);

  private color = new THREE.Color(STATE_COLOR.booting);
  private targetColor = new THREE.Color(STATE_COLOR.booting);
  private state: JarvisState = "booting";
  private speech = 0;
  private speechTarget = 0;
  private mic = 0;
  private micTarget = 0;
  private visible = true;
  private raf = 0;
  private spin = 0;

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, premultipliedAlpha: false, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x000000, 0); // transparent — the Qt window shows the desktop through it
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5)); // DPR cap: 2880x1800 panel would be 4x the pixels

    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
    this.camera.position.set(0, 0, 6);

    // --- particle sphere
    const count = 6000;
    const pos = new Float32Array(count * 3);
    const seed = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      // Fibonacci sphere for an even distribution
      const y = 1 - (i / (count - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const phi = i * Math.PI * (3 - Math.sqrt(5));
      pos[i * 3] = Math.cos(phi) * r * 1.15;
      pos[i * 3 + 1] = y * 1.15;
      pos[i * 3 + 2] = Math.sin(phi) * r * 1.15;
      seed[i] = Math.random();
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    pg.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    this.particleMat = new THREE.ShaderMaterial({
      vertexShader: PARTICLE_VERT,
      fragmentShader: PARTICLE_FRAG,
      uniforms: { uTime: { value: 0 }, uLevel: { value: 0 }, uBreath: { value: 0.12 }, uSize: { value: 1.5 }, uAlpha: { value: 0.28 }, uColor: { value: this.color } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    this.particles = new THREE.Points(pg, this.particleMat);
    this.scene.add(this.particles);

    // --- reactor rings
    const ringDefs = [
      { r: 1.55, tube: 0.016, tilt: 0, speed: 0.15 },
      { r: 1.75, tube: 0.024, tilt: Math.PI / 2.6, speed: -0.22 },
      { r: 1.95, tube: 0.01, tilt: Math.PI / 4, speed: 0.08 },
    ];
    for (const d of ringDefs) {
      const m = new THREE.MeshBasicMaterial({ color: this.color, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending });
      const ring = new THREE.Mesh(new THREE.TorusGeometry(d.r, d.tube, 8, 160), m);
      ring.rotation.x = d.tilt;
      ring.userData.speed = d.speed;
      this.rings.push(ring);
      this.scene.add(ring);
    }

    // --- tick marks around the outer ring (the "HUD gauge" look)
    const tickPts: number[] = [];
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const len = i % 6 === 0 ? 0.14 : 0.06;
      tickPts.push(Math.cos(a) * 2.1, Math.sin(a) * 2.1, 0, Math.cos(a) * (2.1 + len), Math.sin(a) * (2.1 + len), 0);
    }
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.Float32BufferAttribute(tickPts, 3));
    this.ticks = new THREE.LineSegments(tg, new THREE.LineBasicMaterial({ color: this.color, transparent: true, opacity: 0.55 }));
    this.scene.add(this.ticks);

    // --- core
    this.coreMat = new THREE.MeshBasicMaterial({ color: this.color, transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending });
    this.core = new THREE.Mesh(new THREE.CircleGeometry(0.34, 48), this.coreMat);
    this.scene.add(this.core);
    const halo = new THREE.Mesh(new THREE.RingGeometry(0.36, 0.5, 64), new THREE.MeshBasicMaterial({ color: this.color, transparent: true, opacity: 0.1, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }));
    this.core.add(halo);

    // --- waveform ring (mic)
    this.waveGeo = new THREE.BufferGeometry();
    this.waveGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(this.waveBars * 6), 3));
    this.wave = new THREE.LineSegments(this.waveGeo, new THREE.LineBasicMaterial({ color: this.color, transparent: true, opacity: 0.0 }));
    this.scene.add(this.wave);

    // --- post
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.45, 0.5, 0.25);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
    this.composer.addPass(new ShaderPass(ALPHA_FROM_LUMA)); // last: restores transparency (see note above)

    new ResizeObserver(() => this.resize()).observe(canvas.parentElement ?? document.body);
    this.resize();
    document.addEventListener("visibilitychange", () => this.setVisible(!document.hidden));
    this.loop();
  }

  // ---- public API --------------------------------------------------------------------------
  setState(s: JarvisState): void {
    this.state = s;
    this.targetColor.setHex(STATE_COLOR[s] ?? STATE_COLOR.idle);
  }
  /** Level of Jarvis' own voice, 0..1, ~18 Hz. Measured with Kokoro bm_george: p50 0.36, p90 0.53, max 0.66,
   *  so a gain of 1.1 puts normal speech around 0.4 and the loudest syllables near 0.75 (no clipping). */
  setSpeechLevel(v: number): void {
    this.speechTarget = Math.min(1, Math.max(0, v) * 1.1);
  }
  /** Mic RMS 0..1 while listening. */
  setMicLevel(v: number): void {
    this.micTarget = Math.min(1, Math.max(0, v) * 3);
  }
  /** Pause rendering when the window is hidden (saves GPU for the models). */
  setVisible(v: boolean): void {
    if (v === this.visible) return;
    this.visible = v;
    if (v) this.loop();
    else cancelAnimationFrame(this.raf);
  }
  /** A quick flash on wake. */
  pulse(): void {
    this.speech = Math.max(this.speech, 0.7);
  }

  // ---- internals ---------------------------------------------------------------------------
  private resize(): void {
    const el = this.canvas.parentElement ?? document.body;
    const w = Math.max(1, el.clientWidth);
    const h = Math.max(1, el.clientHeight);
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    // Particle density compensation: the reactor is fitted to the shorter side, so on a 340 px compact window the
    // same 6000 points land on ~7x fewer pixels than on a 900 px panel. Shrink + fade them to keep the look identical.
    const k = Math.min(1, Math.max(0.35, Math.min(w, h) / 900));
    this.particleMat.uniforms.uSize.value = 1.5 * Math.max(0.6, k);
    this.particleMat.uniforms.uAlpha.value = 0.28 * k;
    // Fit the reactor (radius ~2.3 incl. ticks) to the shorter side with a little margin.
    const fit = 2.6;
    const vfov = (this.camera.fov * Math.PI) / 180;
    const distH = fit / Math.tan(vfov / 2);
    const distW = fit / (Math.tan(vfov / 2) * this.camera.aspect);
    this.camera.position.z = Math.max(distH, distW);
    this.camera.updateProjectionMatrix();
  }

  private loop = (): void => {
    if (!this.visible) return;
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;

    // smoothing: fast attack, slow release -> punchy syllables, graceful decay
    this.speech = smooth(this.speech, this.speechTarget, 0.55, 0.12);
    this.mic = smooth(this.mic, this.micTarget, 0.5, 0.15);
    this.color.lerp(this.targetColor, 0.08);

    const thinking = this.state === "thinking";
    const listening = this.state === "listening" || this.state === "confirming";
    const level = this.speech + (thinking ? 0.12 + 0.1 * Math.sin(t * 9) : 0);

    // particles
    this.particleMat.uniforms.uTime.value = t;
    this.particleMat.uniforms.uLevel.value = level;
    this.particleMat.uniforms.uBreath.value = thinking ? 0.22 : 0.12;
    this.particles.rotation.y += dt * (0.08 + level * 0.6 + (thinking ? 0.5 : 0));
    this.particles.rotation.x = Math.sin(t * 0.2) * 0.15;

    // rings
    this.spin += dt * (1 + level * 3 + (thinking ? 4 : 0));
    for (const r of this.rings) {
      r.rotation.z = this.spin * (r.userData.speed as number);
      r.rotation.y += dt * (r.userData.speed as number) * 0.4;
      const m = r.material as THREE.MeshBasicMaterial;
      m.color.copy(this.color);
      m.opacity = 0.6 + level * 0.4;
    }
    (this.ticks.material as THREE.LineBasicMaterial).color.copy(this.color);
    this.ticks.rotation.z = -this.spin * 0.03;

    // core
    this.coreMat.color.copy(this.color);
    this.coreMat.opacity = 0.2 + level * 0.5;
    const cs = 1 + level * 0.25 + Math.sin(t * 2) * 0.02;
    this.core.scale.setScalar(cs);
    (this.core.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>).material.color.copy(this.color);

    // waveform: shift history, write new mic sample, build bar segments on a ring of radius 1.35
    this.micHistory.copyWithin(1, 0, this.waveBars - 1);
    this.micHistory[0] = this.mic;
    const wm = this.wave.material as THREE.LineBasicMaterial;
    wm.color.copy(this.color);
    wm.opacity += ((listening ? 0.85 : 0) - wm.opacity) * 0.1;
    if (wm.opacity > 0.01) {
      const arr = this.waveGeo.getAttribute("position") as THREE.BufferAttribute;
      const a = arr.array as Float32Array;
      for (let i = 0; i < this.waveBars; i++) {
        const ang = (i / this.waveBars) * Math.PI * 2 + this.spin * 0.05;
        const target = 0.03 + this.micHistory[i] * 0.45 + (listening ? 0.02 * Math.sin(t * 6 + i * 0.3) : 0);
        this.waveHeights[i] += (target - this.waveHeights[i]) * 0.3;
        const r0 = 1.35;
        const r1 = r0 + this.waveHeights[i];
        a[i * 6] = Math.cos(ang) * r0; a[i * 6 + 1] = Math.sin(ang) * r0; a[i * 6 + 2] = 0;
        a[i * 6 + 3] = Math.cos(ang) * r1; a[i * 6 + 4] = Math.sin(ang) * r1; a[i * 6 + 5] = 0;
      }
      arr.needsUpdate = true;
    }

    // bloom breathes with the voice
    this.bloom.strength = 0.4 + level * 0.4;

    this.composer.render();
  };
}

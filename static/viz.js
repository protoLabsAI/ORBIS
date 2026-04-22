// Voice-reactive fractal orb visualizer.
//
// Shader + preset values by sabosugi (https://codepen.io/sabosugi/pen/EagJwmv),
// kept verbatim. The driver below is our own and follows conventions from
// shipping voice UIs (LiveKit Aura, ChatGPT Advanced Voice, Siri, Material
// Motion): asymmetric envelope follower, 4-state machine with ~300 ms
// crossfades, idle breathing at ~0.10 Hz, radial push/pull cue for
// who's-speaking, HSL saturation/luminance shift for state color.

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

// --- Presets — complementary palettes ---------------------------------------
// Each pair places primary and secondary at or near 180° on the color wheel
// so the secondary→primary gradient reads as a clean contrast through the
// fractal shell rather than a subtle hue shift.
export const PRESETS = {
  // Sky / pink — soft cool/warm complementary, reads as "calm tech."
  Aurora: {
    primaryEnergy: '#0ea5e9', secondaryEnergy: '#f472b6', speed: 0.5, density: 2.4, dpr: 0.7,
    atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.80,
    internalAnim: 0.42, fractalIters: 4, fractalScale: 0.85, fractalDecay: -17.0,
    smoothness: 0.032, asymmetry: 0.50, chromaticAberration: 0.022,
  },
  // Orange / indigo — classic fire/ice complementary, high energy.
  Ember: {
    primaryEnergy: '#f97316', secondaryEnergy: '#4338ca', speed: 0.6, density: 2.1, dpr: 0.7,
    atmosphereGlow: 0.22, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.70,
    internalAnim: 0.55, fractalIters: 4, fractalScale: 0.90, fractalDecay: -15.5,
    smoothness: 0.028, asymmetry: 0.60, chromaticAberration: 0.026,
  },
  // Gold / violet — pure complementary on the yellow/violet axis.
  Citrus: {
    primaryEnergy: '#eab308', secondaryEnergy: '#a855f7', speed: 0.55, density: 1.8, dpr: 0.7,
    atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.55,
    internalAnim: 0.45, fractalIters: 3, fractalScale: 0.78, fractalDecay: -14.8,
    smoothness: 0.014, asymmetry: 0.38, chromaticAberration: 0.022,
  },
  // Emerald / rose — green/magenta complementary, lush + punchy.
  Forest: {
    primaryEnergy: '#10b981', secondaryEnergy: '#db2777', speed: 0.9, density: 1.6, dpr: 0.7,
    atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.60,
    internalAnim: 0.42, fractalIters: 4, fractalScale: 0.86, fractalDecay: -22.0,
    smoothness: 0.060, asymmetry: 0.30, chromaticAberration: 0.006,
  },
  // Off-white / near-black — minimal monochrome.
  Noir: {
    primaryEnergy: '#e4e4e7', secondaryEnergy: '#18181b', speed: 0.35, density: 1.0, dpr: 0.7,
    atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.45,
    internalAnim: 0.22, fractalIters: 4, fractalScale: 0.76, fractalDecay: -20.0,
    smoothness: 0.034, asymmetry: 0.10, chromaticAberration: 0.016,
  },
};

// --- Shaders (verbatim from the pen) ----------------------------------------
const vertexShader = /* glsl */ `
  varying vec3 vLocalPosition;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vLocalPosition = position;
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform vec3 uLocalCamPos;
  uniform vec3 uPrimaryColor;
  uniform vec3 uSecondaryColor;
  uniform float uDensity;
  uniform float uFractalIters;
  uniform float uFractalScale;
  uniform float uFractalDecay;
  uniform float uInternalAnim;
  uniform float uSmoothness;
  uniform float uAsymmetry;
  uniform float uAtmosphereGlow;
  uniform vec3 uClickDir;
  uniform float uClickStrength;
  varying vec3 vLocalPosition;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  float evaluateStructure(vec3 pos) {
    float densityAcc = 0.0;
    vec3 anchor = pos;
    float animTime = uTime * uInternalAnim;
    float s = sin(animTime);
    float c = cos(animTime);
    mat2 rotAnim = mat2(c, s, -s, c);
    float a = 0.5 * uAsymmetry;
    mat2 rotAsym1 = mat2(cos(a), sin(a), -sin(a), cos(a));
    float b = 0.3 * uAsymmetry;
    mat2 rotAsym2 = mat2(cos(b), sin(b), -sin(b), cos(b));
    for (int step = 0; step < 12; ++step) {
      if (float(step) >= uFractalIters) break;
      pos.xy *= rotAnim;
      pos.yz *= rotAnim;
      pos.xz *= rotAsym1;
      pos.yz *= rotAsym2;
      pos += vec3(0.05, -0.02, 0.03) * uAsymmetry;
      vec3 foldedPos = sqrt(pos * pos + uSmoothness);
      float magnitudeSq = dot(foldedPos, foldedPos);
      magnitudeSq = max(magnitudeSq, 0.00001);
      pos = (uFractalScale * foldedPos / magnitudeSq) - uFractalScale;
      float ySq = pos.y * pos.y;
      float zSq = pos.z * pos.z;
      float yz2 = 2.0 * pos.y * pos.z;
      pos.yz = vec2(ySq - zSq, yz2);
      pos = vec3(pos.z, pos.x, pos.y);
      densityAcc += exp(uFractalDecay * abs(dot(pos, anchor)));
    }
    return densityAcc * 0.5;
  }

  vec2 getVolumeBounds(vec3 origin, vec3 dir, float radius) {
    float b = dot(origin, dir);
    float c = dot(origin, origin) - radius * radius;
    float discriminant = b * b - c;
    if (discriminant < 0.0) return vec2(-1.0);
    float root = sqrt(discriminant);
    return vec2(-b - root, -b + root);
  }

  vec3 traceEnergy(vec3 origin, vec3 dir, vec2 limits) {
    float currentDepth = limits.x;
    float marchStep = 0.02;
    vec3 finalEnergy = vec3(0.0);
    float fieldVal = 0.0;
    for (int i = 0; i < 64; i++) {
      currentDepth += marchStep * exp(-2.0 * fieldVal);
      if (currentDepth > limits.y) break;
      vec3 samplePoint = origin + currentDepth * dir;
      fieldVal = evaluateStructure(samplePoint);
      float vSq = fieldVal * fieldVal;
      // Single clean secondary→primary ramp, then a white-tipped highlight
      // (capped at 30 %) for the densest samples. No additive overshoot.
      float g   = smoothstep(0.05, 0.75, fieldVal);
      float hot = smoothstep(0.70, 1.00, fieldVal);
      vec3 baseGradient = mix(uSecondaryColor, uPrimaryColor, g);
      vec3 hotColor = mix(uPrimaryColor, vec3(1.0), 0.30);
      vec3 currentGradient = mix(baseGradient, hotColor, hot * 0.55);
      vec3 emission = currentGradient * (fieldVal * 1.8 + vSq * 1.0);
      finalEnergy = 0.99 * finalEnergy + (0.08 * uDensity) * emission;
    }
    return finalEnergy;
  }

  void main() {
    vec3 rayOrig = uLocalCamPos;
    vec3 rayDir = normalize(vLocalPosition - uLocalCamPos);
    float t = uTime * 0.1;
    float s = sin(t);
    float c = cos(t);
    mat2 rotXZ = mat2(c, s, -s, c);
    rayOrig.xz *= rotXZ;
    rayDir.xz *= rotXZ;
    vec2 limits = getVolumeBounds(rayOrig, rayDir, 2.0);
    // When camera is inside the sphere, the near-intersection sits behind
    // us (limits.x < 0). Clamp start to camera origin (0) so the ray marches
    // from the camera outward to the far side, not from behind.
    limits.x = max(limits.x, 0.0);
    if (limits.y < 0.0) discard;  // sphere entirely behind the camera (impossible here but safe)
    vec3 volumeColor = traceEnergy(rayOrig, rayDir, limits);
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float facingRatio = max(dot(normal, viewDir), 0.0);
    float edgeAA = smoothstep(0.0, 0.05, facingRatio);
    vec3 finalColor = 0.5 * log(1.0 + volumeColor);
    finalColor = clamp(finalColor, 0.0, 1.0);
    finalColor *= edgeAA;
    float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
    float alpha = clamp(maxLuma * 1.5, 0.0, 1.0) * edgeAA;

    // Click bloom — tight spot centered on uClickDir. Raising the
    // smoothstep lower bound from 0.25 → 0.75 narrows the lit cone
    // from ~76° to ~41°, so the bloom reads as a focused touch rather
    // than a whole-hemisphere glow.
    vec3 localNormal = normalize(vLocalPosition);
    float clickBoost = smoothstep(0.75, 1.0, dot(localNormal, uClickDir)) * uClickStrength;
    finalColor += uPrimaryColor * clickBoost * 0.9;
    alpha = clamp(alpha + clickBoost * 0.4, 0.0, 1.0);

    gl_FragColor = vec4(finalColor, alpha);
  }
`;

const atmosphereVertexShader = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vLocalPos;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    vLocalPos = position;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const atmosphereFragmentShader = /* glsl */ `
  uniform vec3 uColor;
  uniform vec3 uColorSecondary;
  uniform float uGlow;
  uniform float uLevel;
  uniform vec3 uClickDir;
  uniform float uClickStrength;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vLocalPos;
  void main() {
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float vdn = max(dot(normal, viewDir), 0.0);
    float edgeFade = smoothstep(0.0, 0.15, vdn);
    float innerFadePoint = clamp(1.0 - uLevel, 0.0, 0.99);
    float centerFade = smoothstep(1.0, innerFadePoint, vdn);
    float alpha = edgeFade * centerFade * uGlow;

    // Radial halo gradient — secondary at the limb, primary pushing inward.
    // Wider smoothstep = more of the ring shows the transition band, not
    // just a hard split between the two hues.
    float gradT = smoothstep(0.0, 0.85, vdn);
    vec3 haloColor = mix(uColorSecondary, uColor, gradT);

    // Click bloom — matched to the tight cone in the core shader; lit
    // area on the halo is a focused spot rather than a wide arc.
    float clickBoost = smoothstep(0.6, 1.0, dot(normalize(vLocalPos), uClickDir)) * uClickStrength;
    alpha *= (1.0 + clickBoost * 3.0);
    haloColor = mix(haloColor, uColor * 1.25, clickBoost * 0.7);

    gl_FragColor = vec4(haloColor, alpha);
  }
`;

const ChromaticAberrationShader = {
  uniforms: { tDiffuse: { value: null }, uAmount: { value: 0.025 } },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uAmount;
    varying vec2 vUv;
    void main() {
      vec4 baseColor = texture2D(tDiffuse, vUv);
      float luma = max(baseColor.r, max(baseColor.g, baseColor.b));
      float mask = smoothstep(0.01, 0.1, luma);
      vec2 offset = (vUv - 0.5) * uAmount;
      float r = texture2D(tDiffuse, vUv + offset).r;
      float g = texture2D(tDiffuse, vUv).g;
      float b = texture2D(tDiffuse, vUv - offset).b;
      vec3 aberratedColor = vec3(r, g, b);
      gl_FragColor = vec4(mix(baseColor.rgb, aberratedColor, mask), 1.0);
    }
  `,
};

// --- Tuning constants -------------------------------------------------------
// Envelope — asymmetric attack/release on the raw RMS. Fast attack catches
// voice onsets; slow release spans the gaps between syllables so the state
// machine doesn't flip on every breath. Bot envelope releases faster so the
// speaking pump decays naturally when the bot stops talking.
const ENV_STAGE2 = 0.22;
const ENV_USER = { attack: 0.22, release: 0.04 };  // smoother user onsets
const ENV_BOT  = { attack: 0.25, release: 0.10 };  // smoother AI pump

// A third "display" smoother on top of the envelope. Kills the last bit of
// judder before values hit uniforms — one-pole EMA per frame. Lower = smoother.
const DISP_ALPHA = 0.10;

// Byte-domain RMS scaling. Silence is ~0.003, shouting peaks ~0.35.
const NORM_FLOOR = 0.020;
const NORM_CEIL  = 0.300;

// State machine thresholds (on the normalized envelope, not raw RMS).
// Entry threshold is higher than exit threshold — classic hysteresis to
// prevent state flip-flopping.
const SPEAK_ENTER = 0.08;
const SPEAK_EXIT  = 0.035;
const LISTEN_MIN_DWELL_MS = 500;   // stay in listening at least this long
const THINK_DWELL_MS = 1400;       // "handoff" beat after user stops
const STATE_XFADE_MS = 600;        // state-to-state interpolation — slower = more deliberate

// Cap per-frame delta so backgrounded tabs / GPU stalls / long GC pauses
// don't translate into huge uTime or rotation jumps on the next frame.
const MAX_DELTA_S = 1 / 30;        // ~33 ms = 30 fps floor

// Global rotation scaling — applies on top of per-state rotation factors.
// Makes the idle spin feel contemplative rather than restless.
const ROTATION_SCALE = 0.45;
// Max drag-spun velocity (rad/s) — clamps wild flings to a smoother spin.
const DRAG_VEL_MAX = 3.5;

// Idle breath — two non-commensurate low-frequency sines for life-without-loops.
const BREATH_HZ_1 = 0.10;
const BREATH_HZ_2 = 0.037;
const BREATH_AMP  = 0.03;   // fraction of scale to modulate

// --- Helpers ----------------------------------------------------------------
const lerp = (a, b, t) => a + (b - a) * t;
const clamp01 = v => v < 0 ? 0 : v > 1 ? 1 : v;
const easeInOutCubic = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

function rmsFromAnalyser(analyser, buf) {
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

// Asymmetric two-stage envelope follower. Maps raw RMS → [0, 1] with heavy
// smoothing and fast-up / slow-down response.
class Envelope {
  constructor({ attack, release }) {
    this.attack = attack;
    this.release = release;
    this.s1 = 0;
    this.s2 = 0;
  }
  update(raw) {
    const k1 = raw > this.s1 ? this.attack : this.release;
    this.s1 += (raw - this.s1) * k1;
    this.s2 += (this.s1 - this.s2) * ENV_STAGE2;
    return clamp01((this.s2 - NORM_FLOOR) / (NORM_CEIL - NORM_FLOOR));
  }
  reset() { this.s1 = 0; this.s2 = 0; }
}

// Shift a color's saturation / luminance while preserving hue.
function withHSL(hex, satMult, lumMult) {
  const c = new THREE.Color(hex);
  const hsl = { h: 0, s: 0, l: 0 };
  c.getHSL(hsl);
  return new THREE.Color().setHSL(
    hsl.h,
    clamp01(hsl.s * satMult),
    clamp01(hsl.l * lumMult),
  );
}

// Per-state target snapshot. These are the "resting" uniform values for a
// given state; audio-driven modulation is added on top during render.
function stateSnapshot(state, base) {
  switch (state) {
    case 'idle':
      return {
        density: base.density * 0.55,
        glow: base.atmosphereGlow * 0.7,
        speed: base.speed * 0.7,
        ca: base.chromaticAberration * 0.4,
        asymmetry: base.asymmetry * 0.8,
        rotation: base.orbRotation * 0.55,
        scale: 0.94,
        primary: withHSL(base.primaryEnergy, 0.80, 0.70),
        secondary: withHSL(base.secondaryEnergy, 0.80, 0.70),
      };
    case 'listening':
      // Small inward pull + slightly cooler; the orb "takes in" but stays alive.
      return {
        density: base.density * 0.80,
        glow: base.atmosphereGlow * 0.85,
        speed: base.speed * 0.65,
        ca: base.chromaticAberration * 0.6,
        asymmetry: base.asymmetry * 0.85,
        rotation: base.orbRotation * 0.55,
        scale: 0.93,                       // inward cue (was 0.88 — too small)
        primary: withHSL(base.primaryEnergy, 0.95, 0.95),
        secondary: withHSL(base.secondaryEnergy, 0.95, 0.95),
      };
    case 'thinking':
      // Neither cool nor warm; slightly faster internal swirl suggesting work.
      return {
        density: base.density * 0.70,
        glow: base.atmosphereGlow * 0.90,
        speed: base.speed * 1.0,
        ca: base.chromaticAberration * 0.7,
        asymmetry: base.asymmetry * 0.9,
        rotation: base.orbRotation * 0.85,
        scale: 0.96,
        primary: withHSL(base.primaryEnergy, 0.95, 0.90),
        secondary: withHSL(base.secondaryEnergy, 0.95, 0.90),
      };
    case 'speaking':
      // Push outward, full saturation/luminance; audio modulation layered on top.
      return {
        density: base.density * 0.90,
        glow: base.atmosphereGlow * 1.10,
        speed: base.speed * 1.05,
        ca: base.chromaticAberration * 1.0,
        asymmetry: base.asymmetry,
        rotation: base.orbRotation * 1.0,
        scale: 1.06,                       // outward cue
        primary: new THREE.Color(base.primaryEnergy),
        secondary: new THREE.Color(base.secondaryEnergy),
      };
  }
}

function lerpSnapshot(a, b, t) {
  return {
    density: lerp(a.density, b.density, t),
    glow: lerp(a.glow, b.glow, t),
    speed: lerp(a.speed, b.speed, t),
    ca: lerp(a.ca, b.ca, t),
    asymmetry: lerp(a.asymmetry, b.asymmetry, t),
    rotation: lerp(a.rotation, b.rotation, t),
    scale: lerp(a.scale, b.scale, t),
    primary: a.primary.clone().lerp(b.primary, t),
    secondary: a.secondary.clone().lerp(b.secondary, t),
  };
}

// --- Main class -------------------------------------------------------------
export class VoiceOrb {
  constructor(container, { preset = 'Aurora' } = {}) {
    this.container = container;
    this.basePreset = { ...PRESETS[preset] };

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x000000);
    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 100);
    // Start at midpoint of the zoom range [6, 20] so the user has room to
    // zoom in AND out from the default view.
    this.camera.position.set(0, 0, 13);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.setPixelRatio(this.basePreset.dpr);
    container.appendChild(this.renderer.domElement);

    this.uniforms = {
      uTime: { value: 0 },
      uLocalCamPos: { value: new THREE.Vector3() },
      uPrimaryColor: { value: new THREE.Color(this.basePreset.primaryEnergy) },
      uSecondaryColor: { value: new THREE.Color(this.basePreset.secondaryEnergy) },
      uDensity: { value: this.basePreset.density },
      uFractalIters: { value: this.basePreset.fractalIters },
      uFractalScale: { value: this.basePreset.fractalScale },
      uFractalDecay: { value: this.basePreset.fractalDecay },
      uInternalAnim: { value: this.basePreset.internalAnim },
      uSmoothness: { value: this.basePreset.smoothness },
      uAsymmetry: { value: this.basePreset.asymmetry },
      uClickDir: { value: new THREE.Vector3(0, 0, 1) },
      uClickStrength: { value: 0 },
    };

    const material = new THREE.ShaderMaterial({
      vertexShader, fragmentShader, uniforms: this.uniforms,
      transparent: true, side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.atmosphereUniforms = {
      uColor: { value: new THREE.Color(this.basePreset.primaryEnergy) },
      uColorSecondary: { value: new THREE.Color(this.basePreset.secondaryEnergy) },
      uGlow: { value: this.basePreset.atmosphereGlow },
      uLevel: { value: this.basePreset.atmosphereLevel },
      uClickDir: this.uniforms.uClickDir,       // shared — set once, read twice
      uClickStrength: this.uniforms.uClickStrength,
    };
    const atmosphereMaterial = new THREE.ShaderMaterial({
      vertexShader: atmosphereVertexShader,
      fragmentShader: atmosphereFragmentShader,
      uniforms: this.atmosphereUniforms,
      transparent: true, side: THREE.FrontSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const geometry = new THREE.SphereGeometry(2.0, 128, 128);
    this.orb = new THREE.Mesh(geometry, material);
    this.scene.add(this.orb);
    this.atmosphereMesh = new THREE.Mesh(geometry, atmosphereMaterial);
    this.atmosphereMesh.scale.setScalar(this.basePreset.atmosphereScale);
    this.orb.add(this.atmosphereMesh);

    this.composer = new EffectComposer(this.renderer);
    this.composer.setPixelRatio(this.basePreset.dpr);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.caPass = new ShaderPass(ChromaticAberrationShader);
    this.caPass.uniforms.uAmount.value = this.basePreset.chromaticAberration;
    this.composer.addPass(this.caPass);

    // Audio analysers (created lazily).
    this._audioCtx = null;
    this._analysers = { local: null, remote: null };
    this._bufs = { local: null, remote: null };
    this._env = {
      local: new Envelope(ENV_USER),
      remote: new Envelope(ENV_BOT),
    };
    // Third-stage display smoothing — the actual values the shader sees.
    this._disp = { bot: 0, user: 0 };

    // State machine.
    this.state = 'idle';
    this._stateSnap = stateSnapshot('idle', this.basePreset);
    this._stateFrom = this._stateSnap;
    this._stateTo   = this._stateSnap;
    this._stateXfadeStart = 0;        // ms
    this._stateXfadeActive = false;
    this._stateEnteredMs = performance.now();
    this._lastUserSpeechMs = -Infinity;

    // Mouse interaction: drag-to-spin with momentum, click-to-pulse.
    this._raycaster = new THREE.Raycaster();
    this._ndc = new THREE.Vector2();
    this._dragging = false;
    this._holding = false;            // pointer is down — keeps bloom lit at cursor
    this._dragMoved = 0;              // px of movement since pointerdown
    this._lastPointer = { x: 0, y: 0, t: 0 };
    this._dragVel = { x: 0, y: 0 };   // radians per second, decays when idle

    // Zoom — scroll wheel moves the camera along its z axis. Smoothed via
    // lerp each frame toward _zoomTarget so a single flick feels inertial.
    this._zoomTarget = this.camera.position.z;
    this._attachPointerHandlers();

    this._startTimeMs = performance.now();
    this.clock = new THREE.Clock();
    // Preallocate scratch vector for the localCam computation in _tick so
    // we don't allocate per frame (GC pressure builds up over long sessions
    // and shows up as intermittent jitter).
    this._scratchCam = new THREE.Vector3();
    this._onResize = this._onResize.bind(this);
    window.addEventListener('resize', this._onResize);

    this._running = true;
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
  }

  _attachPointerHandlers() {
    const dom = this.renderer.domElement;
    dom.style.touchAction = 'none';

    const down = (e) => {
      dom.setPointerCapture(e.pointerId);
      this._dragging = true;
      this._holding = true;
      this._dragMoved = 0;
      this._lastPointer = { x: e.clientX, y: e.clientY, t: performance.now() };
      this._dragVel.x = 0;
      this._dragVel.y = 0;
      // Bloom tracks the cursor immediately on press.
      this._pulseAt(e.clientX, e.clientY);
    };
    const releaseDrag = () => {
      if (!this._dragging) return;
      this._dragging = false;
      this._holding = false;
      this._dragVel.x = 0;
      this._dragVel.y = 0;
    };
    const move = (e) => {
      if (!this._dragging) return;
      // Safety: if the pointer is no longer pressed (missed pointerup —
      // can happen on devtools focus steal, alt-tab mid-click, etc.)
      // release drag state NOW. Without this, captured pointer moves leak
      // into _dragVel and auto-spin the orb.
      if (e.buttons === 0) { releaseDrag(); return; }
      const nowMs = performance.now();
      const dt = Math.max(1, nowMs - this._lastPointer.t) / 1000;
      const dx = e.clientX - this._lastPointer.x;
      const dy = e.clientY - this._lastPointer.y;
      this._dragMoved += Math.hypot(dx, dy);
      const SENSITIVITY = 0.003;  // radians per pixel
      this.orb.rotation.y += dx * SENSITIVITY;
      this.orb.rotation.x += dy * SENSITIVITY;
      const clamp = (v, m) => Math.max(-m, Math.min(m, v));
      const instVy = clamp((dx * SENSITIVITY) / dt, DRAG_VEL_MAX);
      const instVx = clamp((dy * SENSITIVITY) / dt, DRAG_VEL_MAX);
      this._dragVel.y = lerp(this._dragVel.y, instVy, 0.5);
      this._dragVel.x = lerp(this._dragVel.x, instVx, 0.5);
      this._lastPointer = { x: e.clientX, y: e.clientY, t: nowMs };
      // Bloom follows the cursor while the pointer is down.
      this._pulseAt(e.clientX, e.clientY);
    };
    const up = (e) => {
      if (!this._dragging) return;
      this._dragging = false;
      this._holding = false;
      try { dom.releasePointerCapture(e.pointerId); } catch (_) {}
      // Strength stays at 1.0 until the _tick decay loop fades it out.
    };

    dom.addEventListener('pointerdown', down);
    dom.addEventListener('pointermove', move);
    dom.addEventListener('pointerup', up);
    dom.addEventListener('pointercancel', up);

    // Scroll wheel → zoom. Positive deltaY = wheel down = zoom out.
    // Sensitivity scales with current distance so the feel stays consistent
    // regardless of how far out you are. Clamp [6, 20]: 6 is the preset
    // resting distance (= max zoomed in, biggest orb); 20 shrinks the orb
    // well down toward a small pip in the viewport.
    const wheel = (e) => {
      e.preventDefault();
      const step = e.deltaY * 0.001 * Math.max(0.5, this._zoomTarget);
      this._zoomTarget = Math.max(6, Math.min(20, this._zoomTarget + step));
    };
    dom.addEventListener('wheel', wheel, { passive: false });

    // Window blur / visibility change — any path that takes focus away
    // mid-drag (alt-tab, devtools focus, OS-level interrupts) should drop
    // the drag rather than leave it captured.
    const onBlur = () => releaseDrag();
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onBlur);
    this._pointerHandlers = { dom, down, move, up, wheel, onBlur };
  }

  _pulseAt(clientX, clientY) {
    // Convert client → NDC → ray → first hit on the orb.
    this._ndc.set(
      (clientX / window.innerWidth) * 2 - 1,
      -(clientY / window.innerHeight) * 2 + 1,
    );
    this._raycaster.setFromCamera(this._ndc, this.camera);
    const hits = this._raycaster.intersectObject(this.orb, false);
    if (!hits.length) {
      // Cursor is off the orb — extinguish any ongoing bloom (including
      // while held, where the decay loop is paused). No fallback spot.
      this.uniforms.uClickStrength.value = 0;
      return;
    }
    const local = this.orb.worldToLocal(hits[0].point.clone()).normalize();
    this.uniforms.uClickDir.value.copy(local);
    this.uniforms.uClickStrength.value = 1.0;
  }

  _onResize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
  }

  setPreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    this.basePreset = { ...p };
    // Swap non-animated uniforms.
    this.uniforms.uFractalIters.value = p.fractalIters;
    this.uniforms.uFractalScale.value = p.fractalScale;
    this.uniforms.uFractalDecay.value = p.fractalDecay;
    this.uniforms.uSmoothness.value   = p.smoothness;
    this.uniforms.uInternalAnim.value = p.internalAnim;
    this.atmosphereUniforms.uLevel.value = p.atmosphereLevel;
    this.atmosphereMesh.scale.setScalar(p.atmosphereScale);
    this.renderer.setPixelRatio(p.dpr);
    this.composer.setPixelRatio(p.dpr);
    // Crossfade into the new preset's current-state snapshot.
    this._beginStateCrossfade(this.state);
  }

  /**
   * Live-edit a single preset parameter. Sliders in the UI call this on
   * every input event. State-driven values (density, glow, speed, CA,
   * asymmetry, internalAnim, orbRotation, colors) re-run the state
   * crossfade so the resting values pick up the new base. Direct-uniform
   * values (fractal*, smoothness, atmosphereLevel, atmosphereScale, dpr)
   * update their uniforms immediately.
   */
  setParam(key, value) {
    if (!(key in this.basePreset)) return;
    this.basePreset[key] = value;
    switch (key) {
      case 'fractalIters':   this.uniforms.uFractalIters.value = value; return;
      case 'fractalScale':   this.uniforms.uFractalScale.value = value; return;
      case 'fractalDecay':   this.uniforms.uFractalDecay.value = value; return;
      case 'smoothness':     this.uniforms.uSmoothness.value   = value; return;
      case 'internalAnim':   this.uniforms.uInternalAnim.value = value; return;
      case 'atmosphereLevel':this.atmosphereUniforms.uLevel.value = value; return;
      case 'atmosphereScale':this.atmosphereMesh.scale.setScalar(value); return;
      case 'dpr':
        this.renderer.setPixelRatio(value);
        this.composer.setPixelRatio(value);
        return;
    }
    // Everything else is state-driven — re-run the crossfade so the
    // target snapshot reads the new base.
    this._beginStateCrossfade(this.state);
  }

  getParams() { return { ...this.basePreset }; }

  attachStream(stream, kind) {
    if (!this._audioCtx) {
      this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (this._audioCtx.state === 'suspended') {
      this._audioCtx.resume().catch(() => {});
    }
    this.detachStream(kind);
    const source = this._audioCtx.createMediaStreamSource(stream);
    const analyser = this._audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.55;
    source.connect(analyser);
    this._analysers[kind] = { source, analyser };
    this._bufs[kind] = new Uint8Array(analyser.fftSize);
    this._env[kind].reset();
  }

  detachStream(kind) {
    const a = this._analysers[kind];
    if (!a) return;
    try { a.source.disconnect(); } catch (_) {}
    this._analysers[kind] = null;
    this._bufs[kind] = null;
    this._env[kind].reset();
  }

  destroy() {
    this._running = false;
    window.removeEventListener('resize', this._onResize);
    if (this._pointerHandlers) {
      const { dom, down, move, up, wheel, onBlur } = this._pointerHandlers;
      dom.removeEventListener('pointerdown', down);
      dom.removeEventListener('pointermove', move);
      dom.removeEventListener('pointerup', up);
      dom.removeEventListener('pointercancel', up);
      dom.removeEventListener('wheel', wheel);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onBlur);
    }
    this.detachStream('local');
    this.detachStream('remote');
    if (this._audioCtx) { try { this._audioCtx.close(); } catch (_) {} this._audioCtx = null; }
    this.renderer.dispose();
    if (this.renderer.domElement.parentNode) {
      this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
    }
  }

  _deriveState(bot, user, nowMs) {
    // Bot overrides everything.
    const botGate = this.state === 'speaking' ? SPEAK_EXIT : SPEAK_ENTER;
    if (bot > botGate) return 'speaking';

    // Hysteresis on user: entering listening needs higher level than staying.
    const userGate = this.state === 'listening' ? SPEAK_EXIT : SPEAK_ENTER;
    const userActive = user > userGate;
    if (userActive) {
      this._lastUserSpeechMs = nowMs;
      return 'listening';
    }

    // Min dwell — once we're in listening, hold at least LISTEN_MIN_DWELL_MS
    // so syllable gaps don't briefly kick us out.
    if (this.state === 'listening' &&
        (nowMs - this._stateEnteredMs) < LISTEN_MIN_DWELL_MS) {
      return 'listening';
    }

    // Brief "thinking" dwell after user stops, before bot starts speaking.
    if (nowMs - this._lastUserSpeechMs < THINK_DWELL_MS) return 'thinking';
    return 'idle';
  }

  _beginStateCrossfade(nextState) {
    // Snapshot where we are visually RIGHT NOW as the "from" so the crossfade
    // starts from current values, not whatever the previous state target was.
    this._stateFrom = { ...this._stateSnap,
      primary: this._stateSnap.primary.clone(),
      secondary: this._stateSnap.secondary.clone() };
    this._stateTo = stateSnapshot(nextState, this.basePreset);
    this._stateXfadeStart = performance.now();
    this._stateXfadeActive = true;
  }

  _tick() {
    if (!this._running) return;
    requestAnimationFrame(this._tick);

    // Clamp delta — a 2-second backgrounded pause shouldn't translate to a
    // 2-second uTime jump or a visible rotation snap on the next frame.
    const delta = Math.min(this.clock.getDelta(), MAX_DELTA_S);
    const nowMs = performance.now();

    // ---- Audio envelopes -------------------------------------------------
    let bot = 0, user = 0;
    if (this._analysers.remote) {
      const raw = rmsFromAnalyser(this._analysers.remote.analyser, this._bufs.remote);
      bot = this._env.remote.update(raw);
    }
    if (this._analysers.local) {
      const raw = rmsFromAnalyser(this._analysers.local.analyser, this._bufs.local);
      user = this._env.local.update(raw);
    }

    // ---- State machine --------------------------------------------------
    const nextState = this._deriveState(bot, user, nowMs);
    if (nextState !== this.state) {
      this.state = nextState;
      this._stateEnteredMs = nowMs;
      this._beginStateCrossfade(nextState);
    }

    // Advance or settle the state crossfade.
    if (this._stateXfadeActive) {
      const t = clamp01((nowMs - this._stateXfadeStart) / STATE_XFADE_MS);
      const e = easeInOutCubic(t);
      this._stateSnap = lerpSnapshot(this._stateFrom, this._stateTo, e);
      if (t >= 1) {
        this._stateXfadeActive = false;
        this._stateSnap = this._stateTo;
      }
    }

    // ---- Idle breath (always on) ----------------------------------------
    const tSec = (nowMs - this._startTimeMs) / 1000;
    const breath = Math.sin(tSec * Math.PI * 2 * BREATH_HZ_1)
                 + 0.5 * Math.sin(tSec * Math.PI * 2 * BREATH_HZ_2);
    const breathNorm = breath * 0.5;  // into roughly [-0.75, 0.75]

    // ---- Display smoothing: final EMA before values hit uniforms --------
    this._disp.bot  = lerp(this._disp.bot,  bot,  DISP_ALPHA);
    this._disp.user = lerp(this._disp.user, user, DISP_ALPHA);
    const dBot  = this._disp.bot;
    const dUser = this._disp.user;

    // ---- Compose final uniforms: state-base + audio modulation ----------
    const s = this._stateSnap;

    // Multipliers deliberately soft — the orb should breathe with the voice,
    // not pulse to every transient. Too strong = jitter; too weak = dead.
    this.uniforms.uDensity.value           = s.density + dBot * 0.9;
    // Asymmetry changes fractal STRUCTURE (per-iteration rotation), so even
    // small input jitter propagates multiplicatively through the folds and
    // reads as twitchy. Keep the user pump tiny (0.06, was 0.15).
    this.uniforms.uAsymmetry.value         = clamp01(s.asymmetry + dUser * 0.06);
    // uInternalAnim is a direct user-controlled knob — no state modulation,
    // no audio pump. Owned by setPreset/setParam.
    this.atmosphereUniforms.uGlow.value    = s.glow + dBot * 1.1 + dUser * 0.35;
    this.caPass.uniforms.uAmount.value     = Math.min(0.05, s.ca + dBot * 0.008);

    // Colors — state encodes saturation/luminance; push directly.
    this.uniforms.uPrimaryColor.value.copy(s.primary);
    this.uniforms.uSecondaryColor.value.copy(s.secondary);
    this.atmosphereUniforms.uColor.value.copy(s.primary);
    this.atmosphereUniforms.uColorSecondary.value.copy(s.secondary);

    // Scale = state × breath × gentle audio pump.
    const scale = s.scale
                  * (1 + breathNorm * BREATH_AMP)
                  * (1 + dBot * 0.06);
    this.orb.scale.setScalar(scale);

    // Time + rotation integrate by delta (continuous, no smoothing needed).
    // Auto-rotation comes from state, plus any user-drag momentum on top.
    // ROTATION_SCALE globally dampens the spin — keeps the motion contemplative.
    this.uniforms.uTime.value += delta * s.speed;
    this.orb.rotation.y += delta * s.rotation * ROTATION_SCALE + this._dragVel.y * delta;
    this.orb.rotation.x += delta * (s.rotation * 0.5) * ROTATION_SCALE + this._dragVel.x * delta;

    // Wrap ever-growing accumulators at multiples of 2π so sin/cos stay
    // numerically clean. GLSL uploads uTime as float32 — over ~10 minutes
    // of runtime the nested sin/cos inside the fractal starts to jitter
    // visibly without this. Wrap point is 2π·N so wrap is invisible.
    const TWO_PI = Math.PI * 2;
    const TIME_WRAP = TWO_PI * 100;
    if (this.uniforms.uTime.value > TIME_WRAP) {
      this.uniforms.uTime.value -= TIME_WRAP;
    }
    if (this.orb.rotation.y > TWO_PI * 50)  this.orb.rotation.y -= TWO_PI * 50;
    if (this.orb.rotation.y < -TWO_PI * 50) this.orb.rotation.y += TWO_PI * 50;
    if (this.orb.rotation.x > TWO_PI * 50)  this.orb.rotation.x -= TWO_PI * 50;
    if (this.orb.rotation.x < -TWO_PI * 50) this.orb.rotation.x += TWO_PI * 50;
    // Decay drag velocity when the user isn't holding the pointer.
    if (!this._dragging) {
      const DAMP = 0.96;  // ~4% drop per frame → ~0.7s half-life at 60fps
      this._dragVel.x *= DAMP;
      this._dragVel.y *= DAMP;
      if (Math.abs(this._dragVel.x) < 0.001) this._dragVel.x = 0;
      if (Math.abs(this._dragVel.y) < 0.001) this._dragVel.y = 0;
    }

    // Decay click-bloom over ~0.6s — but not while the pointer is held down.
    if (!this._holding && this.uniforms.uClickStrength.value > 0) {
      this.uniforms.uClickStrength.value *= 0.93;
      if (this.uniforms.uClickStrength.value < 0.01) {
        this.uniforms.uClickStrength.value = 0;
      }
    }

    // Ease camera toward the zoom target. 0.15 per frame gives a ~300 ms
    // feel — the flick is responsive but not jumpy.
    this.camera.position.z = lerp(this.camera.position.z, this._zoomTarget, 0.15);

    // ---- Render ---------------------------------------------------------
    this.orb.updateMatrixWorld();
    this._scratchCam.copy(this.camera.position);
    this.orb.worldToLocal(this._scratchCam);
    this.uniforms.uLocalCamPos.value.copy(this._scratchCam);

    this.composer.render();
  }
}

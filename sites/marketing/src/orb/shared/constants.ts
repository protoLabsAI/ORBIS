/**
 * Tuning constants pulled from the original viz.js. These numbers
 * govern the "feel" of the orb — envelope response, hysteresis,
 * state dwell, idle breath, rotation damping. Changing them should
 * be a deliberate UX call.
 *
 * Source: viz.js tuning section (~lines 269–308 of the original file).
 */

/** Second-stage envelope smoothing (one-pole EMA) on top of the attack/release stage. */
export const ENV_STAGE2 = 0.22;

/**
 * Asymmetric attack / release per source. Fast attack catches onsets;
 * slow release spans syllable gaps so the state machine doesn't flip
 * on every breath. Bot envelope releases a touch faster so the
 * "speaking pump" decays naturally when the bot stops talking.
 */
export const ENV_USER = { attack: 0.22, release: 0.04 } as const;
export const ENV_BOT  = { attack: 0.25, release: 0.10 } as const;

/**
 * Third-stage "display" smoother applied right before uniforms. Kills
 * the last bit of judder. Lower = smoother = less responsive.
 */
export const DISP_ALPHA = 0.10;

/** Byte-domain RMS scaling. Silence ~0.003, shouting peaks ~0.35. */
export const NORM_FLOOR = 0.020;
export const NORM_CEIL  = 0.300;

/**
 * State-machine thresholds on the normalized envelope (not raw RMS).
 * Entry > exit — classic hysteresis, prevents state flip-flop.
 */
export const SPEAK_ENTER = 0.08;
export const SPEAK_EXIT  = 0.035;

/** Once in listening, hold at least this long so syllable gaps don't eject us. */
export const LISTEN_MIN_DWELL_MS = 500;

/** "Thinking" dwell after user stops, before bot starts speaking. */
export const THINK_DWELL_MS = 1400;

/** State-to-state crossfade duration. Slower = more deliberate. */
export const STATE_XFADE_MS = 600;

/**
 * Cap per-frame delta so backgrounded tabs / GPU stalls / long GC
 * pauses don't produce huge uTime jumps or rotation snaps. ~33 ms = 30 fps floor.
 */
export const MAX_DELTA_S = 1 / 30;

/**
 * Global rotation scaling — applies on top of per-state rotation.
 * Keeps the idle spin contemplative rather than restless.
 */
export const ROTATION_SCALE = 0.45;

/** Max drag-spun velocity (rad/s). Clamps wild flings to a smoother spin. */
export const DRAG_VEL_MAX = 3.5;

/** Idle breath — two non-commensurate low-frequency sines for life-without-loops. */
export const BREATH_HZ_1 = 0.10;
export const BREATH_HZ_2 = 0.037;
/** Fraction of the state scale to modulate. */
export const BREATH_AMP  = 0.03;

/** Camera zoom range along Z. 6 = closest (biggest orb), 20 = pip. */
export const ZOOM_MIN = 6;
export const ZOOM_MAX = 20;
/** Eased zoom lerp — 0.15/frame ≈ 300 ms feel. */
export const ZOOM_LERP = 0.15;

/** Drag momentum damping per frame. ~0.7 s half-life at 60 fps. */
export const DRAG_DAMP = 0.96;

/** Click-bloom decay per frame. */
export const CLICK_DECAY = 0.93;

/** Wrap uTime + rotation at 2π·N so float32 precision doesn't drift. */
export const TWO_PI = Math.PI * 2;
export const TIME_WRAP = TWO_PI * 100;
export const ROT_WRAP  = TWO_PI * 50;

/** Drag pointer sensitivity (radians per pixel). */
export const DRAG_SENSITIVITY = 0.003;

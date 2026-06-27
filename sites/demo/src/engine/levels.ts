/**
 * Live audio-level bus (raw RMS, ~0..0.3) shared with the orb.
 *
 * The Tauri shim's get_audio_levels reads this; the orb's useAudioEnvelopes
 * gains it (mic ×6, playback ×4) into a punchy reactive range. So mic
 * capture and TTS playback drive the orb pulse exactly like the native
 * Rust engine's levels — no orb changes needed.
 */
export interface Levels {
  mic: number;
  playback: number;
}

const levels: Levels = { mic: 0, playback: 0 };

export const getLevels = (): Levels => levels;
export const setMic = (v: number): void => {
  levels.mic = v;
};
export const setPlayback = (v: number): void => {
  levels.playback = v;
};

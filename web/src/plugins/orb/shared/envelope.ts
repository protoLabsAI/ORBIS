// Envelope moved to @orbis/orb-runtime (shared with the orb editor).
// rmsFromAnalyser stays here — it's a browser AnalyserNode helper used
// only by the app's MediaStream fallback path.
export { Envelope } from '@orbis/orb-runtime';

/**
 * Byte-domain RMS from an AnalyserNode time-domain sample.
 *
 * `getByteTimeDomainData` is typed as expecting `Uint8Array<ArrayBuffer>`
 * in TS 6 / lib.dom.d.ts, but the buffer we create via `new Uint8Array(n)`
 * is inferred as `Uint8Array<ArrayBufferLike>`. Cast at the DOM-call site
 * rather than threading the generic through every caller.
 */
export function rmsFromAnalyser(analyser: AnalyserNode, buf: Uint8Array): number {
  analyser.getByteTimeDomainData(buf as Uint8Array<ArrayBuffer>);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

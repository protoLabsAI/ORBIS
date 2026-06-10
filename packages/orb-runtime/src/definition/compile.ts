import * as THREE from 'three';
import type { OrbDefinition } from './types';
import { buildFragmentSource, vertexSource } from './prelude';
import { buildUniforms } from '../engine/uniforms';

export interface CompileCheckResult {
  ok: boolean;
  /** Driver shader info log on failure (best-effort; empty when the GPU gives nothing). */
  log: string;
}

/**
 * Compile a definition's shader program against a real WebGL context and
 * report errors *synchronously* — three.js normally surfaces GLSL
 * compile failures as async console noise long after the material
 * mounted. Used at import time (reject broken orbs before they reach
 * the registry) and by the editor for inline diagnostics.
 *
 * Browser-only (needs WebGL). Creates and disposes a throwaway renderer.
 */
export function compileCheck(def: OrbDefinition): CompileCheckResult {
  let renderer: THREE.WebGLRenderer | null = null;
  let material: THREE.ShaderMaterial | null = null;
  let geometry: THREE.SphereGeometry | null = null;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 4;
    canvas.height = 4;
    renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false });

    let captured = '';
    renderer.debug.checkShaderErrors = true;
    renderer.debug.onShaderError = (gl, _program, vs, fs) => {
      const vLog = gl.getShaderInfoLog(vs) ?? '';
      const fLog = gl.getShaderInfoLog(fs) ?? '';
      captured = [fLog.trim(), vLog.trim()].filter(Boolean).join('\n');
    };

    material = new THREE.ShaderMaterial({
      uniforms: buildUniforms(def),
      vertexShader: vertexSource(def),
      fragmentShader: buildFragmentSource(def),
    });
    geometry = new THREE.SphereGeometry(1, 4, 4);
    const scene = new THREE.Scene();
    scene.add(new THREE.Mesh(geometry, material));
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);

    renderer.compile(scene, camera);

    // three only invokes onShaderError when linking failed; a clean
    // compile leaves `captured` empty.
    if (captured) return { ok: false, log: captured };
    return { ok: true, log: '' };
  } catch (e) {
    return { ok: false, log: e instanceof Error ? e.message : String(e) };
  } finally {
    try { material?.dispose(); } catch { /* best-effort cleanup */ }
    try { geometry?.dispose(); } catch { /* best-effort cleanup */ }
    try { renderer?.dispose(); } catch { /* best-effort cleanup */ }
  }
}

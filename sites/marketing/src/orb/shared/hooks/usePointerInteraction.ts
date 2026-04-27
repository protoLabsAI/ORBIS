import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { lerp } from '../math';
import {
  CLICK_DECAY,
  DRAG_DAMP,
  DRAG_SENSITIVITY,
  DRAG_VEL_MAX,
  ZOOM_LERP,
  ZOOM_MAX,
  ZOOM_MIN,
} from '../constants';

export interface PointerInteraction {
  clickDirRef: React.RefObject<THREE.Vector3>;
  clickStrengthRef: React.RefObject<number>;
  dragVelRef: React.RefObject<{ x: number; y: number }>;
}

/**
 * Binds canvas pointer handlers — drag to spin, click to pulse, wheel
 * to zoom. Decays momentum / bloom / zoom each frame.
 *
 * Exposes three refs:
 *   - clickDirRef / clickStrengthRef: the local-space direction of
 *     the last pulse and its current intensity. Variants write these
 *     to uniforms (uClickDir, uClickStrength) to render the bloom.
 *   - dragVelRef: drag momentum in rad/s (applied to mesh.rotation
 *     by the variant alongside state-driven auto-rotation).
 *
 * Zoom is applied directly to the camera — no variant action needed.
 */
export function usePointerInteraction(
  meshRef: React.RefObject<THREE.Mesh | null>,
): PointerInteraction {
  const { camera, gl } = useThree();

  const clickDirRef = useRef(new THREE.Vector3(0, 0, 1));
  const clickStrengthRef = useRef(0);
  const dragVelRef = useRef({ x: 0, y: 0 });

  const zoomTargetRef = useRef(camera.position.z);
  const raycaster = useMemo(() => new THREE.Raycaster(), []);
  const ndc = useMemo(() => new THREE.Vector2(), []);

  const dragRef = useRef({
    dragging: false,
    holding: false,
    lastX: 0,
    lastY: 0,
    lastT: 0,
  });

  const pulseAt = (clientX: number, clientY: number) => {
    const mesh = meshRef.current;
    if (!mesh) return;
    ndc.set(
      (clientX / window.innerWidth) * 2 - 1,
      -(clientY / window.innerHeight) * 2 + 1,
    );
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObject(mesh, false);
    if (!hits.length) {
      clickStrengthRef.current = 0;
      return;
    }
    const local = mesh.worldToLocal(hits[0].point.clone()).normalize();
    clickDirRef.current.copy(local);
    clickStrengthRef.current = 1.0;
  };

  useEffect(() => {
    const dom = gl.domElement;
    dom.style.touchAction = 'none';

    const down = (e: PointerEvent) => {
      dom.setPointerCapture(e.pointerId);
      const d = dragRef.current;
      d.dragging = true;
      d.holding = true;
      d.lastX = e.clientX;
      d.lastY = e.clientY;
      d.lastT = performance.now();
      dragVelRef.current.x = 0;
      dragVelRef.current.y = 0;
      pulseAt(e.clientX, e.clientY);
    };
    const release = () => {
      const d = dragRef.current;
      if (!d.dragging) return;
      d.dragging = false;
      d.holding = false;
      dragVelRef.current.x = 0;
      dragVelRef.current.y = 0;
    };
    const move = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d.dragging) return;
      if (e.buttons === 0) { release(); return; }
      const nowMs = performance.now();
      const dt = Math.max(1, nowMs - d.lastT) / 1000;
      const dx = e.clientX - d.lastX;
      const dy = e.clientY - d.lastY;
      const mesh = meshRef.current;
      if (mesh) {
        mesh.rotation.y += dx * DRAG_SENSITIVITY;
        mesh.rotation.x += dy * DRAG_SENSITIVITY;
      }
      const clamp = (v: number, m: number) => Math.max(-m, Math.min(m, v));
      const instVy = clamp((dx * DRAG_SENSITIVITY) / dt, DRAG_VEL_MAX);
      const instVx = clamp((dy * DRAG_SENSITIVITY) / dt, DRAG_VEL_MAX);
      dragVelRef.current.y = lerp(dragVelRef.current.y, instVy, 0.5);
      dragVelRef.current.x = lerp(dragVelRef.current.x, instVx, 0.5);
      d.lastX = e.clientX;
      d.lastY = e.clientY;
      d.lastT = nowMs;
      pulseAt(e.clientX, e.clientY);
    };
    const up = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d.dragging) return;
      d.dragging = false;
      d.holding = false;
      try { dom.releasePointerCapture(e.pointerId); } catch {}
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const step = e.deltaY * 0.001 * Math.max(0.5, zoomTargetRef.current);
      zoomTargetRef.current = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomTargetRef.current + step));
    };
    const onBlur = () => release();

    dom.addEventListener('pointerdown', down);
    dom.addEventListener('pointermove', move);
    dom.addEventListener('pointerup', up);
    dom.addEventListener('pointercancel', up);
    dom.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onBlur);

    return () => {
      dom.removeEventListener('pointerdown', down);
      dom.removeEventListener('pointermove', move);
      dom.removeEventListener('pointerup', up);
      dom.removeEventListener('pointercancel', up);
      dom.removeEventListener('wheel', wheel);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onBlur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl, camera, raycaster, ndc]);

  useFrame(() => {
    const d = dragRef.current;
    const v = dragVelRef.current;
    // Drag momentum decay when the user isn't holding.
    if (!d.dragging) {
      v.x *= DRAG_DAMP;
      v.y *= DRAG_DAMP;
      if (Math.abs(v.x) < 0.001) v.x = 0;
      if (Math.abs(v.y) < 0.001) v.y = 0;
    }
    // Click bloom decay — only when pointer is not held.
    if (!d.holding && clickStrengthRef.current > 0) {
      clickStrengthRef.current *= CLICK_DECAY;
      if (clickStrengthRef.current < 0.01) clickStrengthRef.current = 0;
    }
    // Zoom ease.
    camera.position.z = lerp(camera.position.z, zoomTargetRef.current, ZOOM_LERP);
  });

  return { clickDirRef, clickStrengthRef, dragVelRef };
}

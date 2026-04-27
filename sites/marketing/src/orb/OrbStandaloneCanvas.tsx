import { Canvas } from '@react-three/fiber';
import { Suspense } from 'react';
import { FractalVariant } from './variants/fractal/FractalVariant';
import type { VoiceState } from './shared/stateSnapshot';

export function OrbStandaloneCanvas({
  voiceState,
  size = 480,
}: {
  voiceState: VoiceState;
  size?: number;
}) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        overflow: 'hidden',
      }}
    >
      <Canvas
        camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 6.1] }}
        dpr={Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1, 1.5)}
        gl={{ antialias: true, alpha: false }}
        style={{ background: '#000000' }}
        onCreated={() => {
          // Give the first frame a tick to paint before signalling ready
          requestAnimationFrame(() => {
            window.dispatchEvent(new CustomEvent('orbis:canvasReady'));
          });
        }}
      >
        <Suspense fallback={null}>
          <FractalVariant voiceState={voiceState} />
        </Suspense>
      </Canvas>
    </div>
  );
}

import { Canvas } from '@react-three/fiber';
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
        boxShadow: '0 0 80px rgba(14,165,233,0.15)',
      }}
    >
      <Canvas
        camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
        dpr={Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1, 1.5)}
        gl={{ antialias: true, alpha: false }}
        style={{ background: '#000000' }}
      >
        <FractalVariant voiceState={voiceState} />
      </Canvas>
    </div>
  );
}

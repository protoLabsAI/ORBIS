import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { Canvas } from '@react-three/fiber';
import { DefinitionOrb, composeBase, type ParamMap } from '@orbis/orb-runtime';
import { store } from '@orbis/editor-ui';
import { LevelSimulator } from './simulator';

/**
 * Live preview — the SAME DefinitionOrb the app renders, fed by the
 * simulator instead of the voice pipeline. Mood defaults compose into
 * params here exactly like the app's useComposedBase does, so authored
 * moodDefaults preview truthfully.
 */
export function Preview() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const sim = useMemo(() => new LevelSimulator(), []);

  useEffect(() => {
    sim.start();
    return () => sim.stop();
  }, [sim]);

  useEffect(() => {
    sim.configure({
      botMode: snap.sim.botMode,
      userMode: snap.sim.userMode,
      botManual: snap.sim.botManual,
      userManual: snap.sim.userManual,
    });
  }, [sim, snap.sim.botMode, snap.sim.userMode, snap.sim.botManual, snap.sim.userManual]);

  const base = useMemo<ParamMap>(
    () =>
      composeBase(
        snap.params,
        undefined,
        snap.definition.moodDefaults ?? undefined,
        snap.sim.voiceState,
        snap.sim.mood,
      ),
    [snap.params, snap.definition, snap.sim.voiceState, snap.sim.mood],
  );

  return (
    <Canvas
      frameloop="always"
      camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
      dpr={typeof base.dpr === 'number' ? base.dpr : 0.7}
      gl={{ antialias: true, alpha: false }}
    >
      <color attach="background" args={['#000000']} />
      <DefinitionOrb
        definition={snap.definition}
        voiceState={snap.sim.voiceState}
        base={base}
        mood={snap.sim.mood}
        botLevelRef={sim.botRef}
        userLevelRef={sim.userRef}
      />
    </Canvas>
  );
}

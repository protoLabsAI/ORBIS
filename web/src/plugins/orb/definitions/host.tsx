import { useSyncExternalStore, type ComponentType } from 'react';
import { Bloom } from '@react-three/postprocessing';
import {
  DefinitionOrb,
  type BloomOpts,
  type OrbDefinition,
  type ParamMap,
} from '@orbis/orb-runtime';
import { registerVariant, type VariantProps } from '../variants/registry';
import { useComposedBase } from '../shared/hooks/useComposedBase';
import { useAudioEnvelopes } from '../shared/hooks/useAudioEnvelopes';
import { moodStore } from '@/plugins/mood/moodStore';

/**
 * App-side host for data-driven orbs. Wires the app's composition
 * (orbStore params + state/mood overrides + simulation pins) and the
 * native audio envelopes into the engine's DefinitionOrb — the same
 * inputs every hand-written variant gets.
 */
function makeDefinitionComponent(def: OrbDefinition): ComponentType<VariantProps> {
  return function DefinitionVariantHost({ voiceState, botStream, localStream }: VariantProps) {
    const { base, effectiveState } = useComposedBase<ParamMap>(voiceState);
    const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
    const mood = useSyncExternalStore(moodStore.subscribe, moodStore.get, moodStore.get);
    return (
      <DefinitionOrb
        definition={def}
        voiceState={effectiveState}
        base={base}
        mood={mood}
        botLevelRef={dBotRef}
        userLevelRef={dUserRef}
      />
    );
  };
}

function makeBloomPost(opts: BloomOpts): ComponentType {
  return function DefinitionBloom() {
    return (
      <Bloom
        intensity={opts.intensity ?? 1.0}
        luminanceThreshold={opts.luminanceThreshold ?? 0.15}
        luminanceSmoothing={opts.luminanceSmoothing ?? 0.7}
        mipmapBlur={opts.mipmapBlur ?? true}
      />
    );
  };
}

/** Wrap a validated OrbDefinition into a normal VariantSpec and register
 * it. Returns the deregister fn (runtime-imported orbs use it on delete). */
export function registerDefinition(def: OrbDefinition): () => void {
  return registerVariant({
    id: def.id,
    name: def.name,
    description: def.description,
    Component: makeDefinitionComponent(def),
    palettes: def.palettes as Record<string, Record<string, unknown>>,
    fields: def.fields,
    defaultPalette: def.defaultPalette,
    premium: def.premium ?? true,
    moodDefaults: def.moodDefaults ?? undefined,
    PostEffects: def.post?.bloom ? makeBloomPost(def.post.bloom) : undefined,
  });
}

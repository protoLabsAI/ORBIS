import { validateOrbDefinition, type OrbDefinition } from '@orbis/orb-runtime';
import starterRaw from './starter.orbis.json';
import prismRaw from './prism.orbis.json';

export interface Template {
  id: string;
  label: string;
  definition: OrbDefinition;
}

function parse(label: string, raw: unknown): Template | null {
  const res = validateOrbDefinition(raw);
  if (!res.ok) {
    console.error(`[editor] template "${label}" invalid:`, res.errors);
    return null;
  }
  return { id: res.def.id, label, definition: res.def };
}

export const TEMPLATES: Template[] = [
  parse('My First Orb — minimal glow', starterRaw),
  parse('Prism — volumetric raymarch', prismRaw),
].filter((t): t is Template => t !== null);

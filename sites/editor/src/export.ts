import { validateOrbDefinition, type OrbDefinition } from '@orbis/orb-runtime';

/** Serialize + download the definition as `<id>.orbis`. */
export function exportDefinition(def: OrbDefinition): void {
  const blob = new Blob([JSON.stringify(def, null, 2) + '\n'], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${def.id}.orbis`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function parseOrbisFile(
  file: File,
): Promise<{ def: OrbDefinition } | { errors: string[] }> {
  let raw: unknown;
  try {
    raw = JSON.parse(await file.text());
  } catch {
    return { errors: ['not valid JSON'] };
  }
  const res = validateOrbDefinition(raw);
  return res.ok ? { def: res.def } : { errors: res.errors };
}

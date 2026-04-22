import { useEffect, useState } from 'react';
import { Field } from '@/components/ui/field';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api, type Skill, type SkillViz } from '@/lib/api';
import { useWhoami, isSkillLocked } from '@/auth/useWhoami';
import { applySkillViz } from '@/plugins/orb/applySkillViz';

/**
 * The effective viz a session will use:
 *   user.pinned_viz (admin override in the user roster) takes priority,
 *   then the skill's own viz block, else nothing.
 */
function effectiveViz(
  skillViz: SkillViz | undefined,
  pinnedViz: Record<string, unknown> | null | undefined,
): SkillViz | null {
  if (pinnedViz) return pinnedViz as SkillViz;
  return skillViz ?? null;
}

export function SkillSelector() {
  const whoami = useWhoami();
  const locked = isSkillLocked(whoami);

  const [skills, setSkills] = useState<Skill[]>([]);
  const [active, setActive] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await api.skills();
      setSkills(r.skills);
      setActive(r.active);
      // Apply the active skill's viz on initial load, letting a pinned
      // viz from the roster override the skill default.
      const activeSpec = r.skills.find((s) => s.slug === r.active);
      applySkillViz(effectiveViz(activeSpec?.viz, whoami?.pinned_viz));
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  useEffect(() => {
    load();
    // Re-run when whoami changes identity (first load resolves from null
    // to the populated snapshot) so the pinned_viz override kicks in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [whoami?.id]);

  const onChange = async (slug: string) => {
    setActive(slug);
    try {
      const r = await api.setSkill(slug);
      if (r.error) {
        setError(r.error);
        return;
      }
      const spec = skills.find((s) => s.slug === slug);
      applySkillViz(effectiveViz(spec?.viz, whoami?.pinned_viz));
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  if (locked) {
    const activeSpec = skills.find((s) => s.slug === active);
    const label =
      activeSpec?.name
      ?? active
      ?? whoami?.allowed_skills?.[0]
      ?? '—';
    return (
      <Field
        label="Skill"
        hint="Pinned by admin"
        error={error}
      >
        <div className="h-9 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-300 flex items-center">
          {label}
        </div>
      </Field>
    );
  }

  return (
    <Field label="Skill" htmlFor="skill" error={error}>
      <Select value={active || undefined} onValueChange={onChange}>
        <SelectTrigger id="skill" className="w-full">
          <SelectValue placeholder="—" />
        </SelectTrigger>
        <SelectContent>
          {skills.map((s) => (
            <SelectItem key={s.slug} value={s.slug}>{s.name ?? s.slug}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

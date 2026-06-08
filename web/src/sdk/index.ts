/**
 * The ORBIS extension SDK — the blessed, stable surface for plugins, widgets,
 * and orb variants. Import from `@/sdk` instead of reaching into internal
 * module paths, so core refactors don't break your extension.
 *
 * Also fair game (their own stable modules, documented in CONTRIBUTING):
 *   - `@/lib/api`         — the backend client + config/types
 *   - `@/components/ui/*` — design-system primitives (Button, Panel, …)
 *   - the design tokens in `@/index.css`
 *
 * Don't import another plugin's folder directly — if you need something shared,
 * lift it to `@/shared` (or here) rather than reaching across plugins.
 */

// — Registration: how an extension plugs in —
export { registerPlugin } from '@/plugins/PluginHost';
export type { Plugin, UISlotName } from '@/plugins/registry';

export { registerWidget } from '@/widgets/registry';
export type {
  WidgetDef,
  WidgetProps,
  WidgetClass,
  WidgetSurface,
} from '@/widgets/registry';

export { registerVariant } from '@/plugins/orb/variants/registry';
export type { VariantSpec, VariantProps } from '@/plugins/orb/variants/registry';

export { registerActions } from '@/command/registry';
export type { CommandAction } from '@/command/registry';

// — Runtime services an extension commonly needs —
export { useVoiceState, useVoiceStateSelector } from '@/voice/hooks';
export type { VoiceState, VoiceSnapshot, ActivationState } from '@/voice/state';
export { pushStatusTransient } from '@/shared/statusBus';

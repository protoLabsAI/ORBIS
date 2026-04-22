import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Panel } from '@/components/ui/panel';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { CustomPresetMap } from '../orb/storage';

/**
 * The preset section of the orb drawer: palette dropdown, saved
 * presets dropdown, and the Save/Randomize/Copy/Reset button row.
 * Presentation-only; callbacks + palette list are lifted.
 */
export function PresetControls({
  palette,
  paletteNames,
  onPaletteChange,
  customMap,
  customName,
  onLoadCustom,
  onDeleteCustom,
  onSaveAs,
  onRandomize,
  onCopy,
  onReset,
  copyLabel,
  disabled,
}: {
  palette: string;
  paletteNames: string[];
  onPaletteChange: (name: string) => void;
  customMap: CustomPresetMap;
  customName: string;
  onLoadCustom: (name: string) => void;
  onDeleteCustom: () => void;
  onSaveAs: () => void;
  onRandomize: () => void;
  onCopy: () => void;
  onReset: () => void;
  copyLabel: string;
  disabled: boolean;
}) {
  const savedNames = Object.keys(customMap).sort();
  const savedEmpty = savedNames.length === 0;

  return (
    <Panel title="Preset">
      <Field label="Palette" htmlFor="palette">
        <Select value={palette} onValueChange={onPaletteChange}>
          <SelectTrigger id="palette" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {paletteNames.map((name) => (
              <SelectItem key={name} value={name}>{name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field label="Saved" htmlFor="custom">
        <div className="flex gap-2">
          <Select
            value={customName}
            onValueChange={onLoadCustom}
            disabled={savedEmpty}
          >
            <SelectTrigger id="custom" className="flex-1">
              <SelectValue placeholder={savedEmpty ? '— no saved presets —' : '—'} />
            </SelectTrigger>
            <SelectContent>
              {savedNames.map((name) => (
                <SelectItem key={name} value={name}>{name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="ghost"
            size="icon"
            onClick={onDeleteCustom}
            disabled={!customName}
            aria-label="Delete saved preset"
            className="h-9 w-9"
          >
            ×
          </Button>
        </div>
      </Field>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onSaveAs} disabled={disabled}>Save as…</Button>
        <Button size="sm" variant="ghost" onClick={onRandomize} disabled={disabled}>Randomize</Button>
        <Button size="sm" variant="ghost" onClick={onCopy} disabled={disabled}>{copyLabel}</Button>
        <Button size="sm" variant="ghost" onClick={onReset} disabled={disabled}>Reset to palette</Button>
      </div>
    </Panel>
  );
}

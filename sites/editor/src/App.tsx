import { useRef, useState, useSyncExternalStore } from 'react';
import { initStore, store } from './state';
import { useResizablePanel } from './useResizablePanel';
import { TEMPLATES } from './templates';
import { exportDefinition, parseOrbisFile } from './export';
import { Preview } from './preview/Preview';
import { SimulatorBar } from './preview/SimulatorBar';
import { ShaderPane } from './panes/ShaderPane';
import { ControlsPane } from './panes/ControlsPane';
import { BindingsPane } from './panes/BindingsPane';
import { JsonPane } from './panes/JsonPane';
import { MetaPane } from './panes/MetaPane';
import { Upload, Download, BookOpen, Bug, PanelRightClose, PanelRightOpen } from 'lucide-react';

initStore(TEMPLATES[0].definition);

const BUG_URL =
  'https://github.com/protoLabsAI/ORBIS/issues/new?labels=bug&title=%5Borb+editor%5D+';
const DOCS_URL = 'https://orbis.protolabs.studio/docs/how-to/create-custom-orbs';
// Shared chrome for the header's ghost icon buttons.
const ICON_BTN =
  'grid h-7 w-7 cursor-pointer place-items-center rounded-md text-fg-subtle transition-colors hover:bg-edge/40 hover:text-fg';

const TABS = ['Shader', 'Controls', 'Bindings', 'JSON', 'Meta'] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const [tab, setTab] = useState<Tab>('Shader');
  const [importErrors, setImportErrors] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const panel = useResizablePanel();

  const onImport = async (file: File | undefined) => {
    if (!file) return;
    const res = await parseOrbisFile(file);
    if ('errors' in res) {
      setImportErrors(res.errors);
    } else {
      setImportErrors([]);
      store().loadDefinition(res.def);
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b border-edge bg-panel px-4 py-2.5">
        <h1 className="text-sm font-semibold tracking-wide">
          <a
            href="https://orbis.protolabs.studio/"
            title="ORBIS home"
            className="transition-opacity hover:opacity-80"
          >
            <span className="bg-gradient-to-r from-brand to-brand-deep bg-clip-text text-transparent">
              ORBIS
            </span>{' '}
            <span className="text-fg-subtle">orb editor</span>
          </a>
        </h1>
        <span className="truncate text-xs text-fg-subtle">
          {snap.definition.name} <code className="text-[10px]">({snap.definition.id})</code>
        </span>

        <div className="ml-auto flex items-center gap-2 text-xs">
          <select
            className="rounded-md border border-edge bg-bg px-2 py-1"
            value=""
            onChange={(e) => {
              const t = TEMPLATES.find((t) => t.id === e.target.value);
              if (t && window.confirm(`Replace the current draft with "${t.label}"?`)) {
                store().clearDraft();
                store().loadDefinition(structuredClone(t.definition));
              }
            }}
          >
            <option value="" disabled>
              New from template…
            </option>
            {TEMPLATES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <input
            ref={fileRef}
            type="file"
            accept=".orbis,application/json"
            className="hidden"
            onChange={(e) => onImport(e.target.files?.[0])}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            title="Import .orbis"
            aria-label="Import .orbis"
            className={ICON_BTN}
          >
            <Upload className="h-4 w-4" strokeWidth={1.75} />
          </button>
          <button
            type="button"
            onClick={() => exportDefinition(store().getSnapshot().definition)}
            title="Export .orbis"
            aria-label="Export .orbis"
            className={ICON_BTN}
          >
            <Download className="h-4 w-4" strokeWidth={1.75} />
          </button>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            title="Docs"
            aria-label="Documentation"
            className={ICON_BTN}
          >
            <BookOpen className="h-4 w-4" strokeWidth={1.75} />
          </a>
          <a
            href={BUG_URL}
            target="_blank"
            rel="noreferrer"
            title="Report a bug"
            aria-label="Report a bug on GitHub"
            className={ICON_BTN}
          >
            <Bug className="h-4 w-4" strokeWidth={1.75} />
          </a>
        </div>
      </header>

      {importErrors.length > 0 && (
        <div className="border-b border-red-900 bg-red-950/40 px-4 py-1.5 text-xs text-red-300">
          import failed: {importErrors.slice(0, 3).join(' · ')}
          <button className="ml-3 underline" onClick={() => setImportErrors([])}>
            dismiss
          </button>
        </div>
      )}

      <div
        className={'relative flex min-h-0 flex-1' + (panel.dragging ? ' cursor-col-resize select-none' : '')}
      >
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1 bg-black">
            <Preview />
          </div>
          <SimulatorBar />
        </div>

        {!panel.collapsed && (
          <>
            {/* Drag to resize · drag in to collapse · double-click to reset · ←/→ to nudge */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize panel"
              aria-valuenow={panel.width}
              aria-valuemin={panel.min}
              aria-valuemax={panel.max}
              aria-valuetext={`Panel ${panel.width}px`}
              tabIndex={0}
              title="Drag to resize · drag in to collapse · double-click to reset"
              {...panel.dividerProps}
              className={
                'w-1.5 shrink-0 cursor-col-resize touch-none outline-none transition-colors ' +
                (panel.dragging ? 'bg-brand' : 'bg-edge hover:bg-brand focus-visible:bg-brand')
              }
            />

            <div className="flex shrink-0 flex-col" style={{ width: panel.width }}>
              <nav className="flex items-center border-b border-edge bg-panel text-xs">
                {TABS.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTab(t)}
                    className={
                      'px-3.5 py-2 transition-colors ' +
                      (tab === t
                        ? 'border-b-2 border-brand text-fg'
                        : 'text-fg-subtle hover:text-fg')
                    }
                  >
                    {t}
                  </button>
                ))}
              </nav>
              <div className="min-h-0 flex-1">
                {tab === 'Shader' && <ShaderPane />}
                {tab === 'Controls' && <ControlsPane />}
                {tab === 'Bindings' && <BindingsPane />}
                {tab === 'JSON' && <JsonPane />}
                {tab === 'Meta' && <MetaPane />}
              </div>
            </div>
          </>
        )}

        {/* Floating panel toggle — top-right, under the header. The single
            manage control now that the collapsed rail is gone (panel fully
            collapses to the bare orb). */}
        <button
          type="button"
          onClick={panel.collapsed ? panel.expand : panel.collapse}
          title={panel.collapsed ? 'Show panel' : 'Hide panel'}
          aria-label={panel.collapsed ? 'Show editor panel' : 'Hide editor panel'}
          aria-pressed={!panel.collapsed}
          className="absolute right-3 top-3 z-10 grid h-8 w-8 cursor-pointer place-items-center rounded-md border border-edge bg-panel/80 text-fg-subtle backdrop-blur transition-colors hover:bg-panel hover:text-fg"
        >
          {panel.collapsed ? (
            <PanelRightOpen className="h-4 w-4" strokeWidth={1.75} />
          ) : (
            <PanelRightClose className="h-4 w-4" strokeWidth={1.75} />
          )}
        </button>
      </div>
    </div>
  );
}

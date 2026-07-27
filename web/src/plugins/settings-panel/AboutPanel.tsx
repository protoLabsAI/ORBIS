import { Panel } from '@/components/ui/panel';
import { ProtoLabsIcon } from '@/components/ProtoLabsIcon';
import { ProtoLabsLink } from '@/components/BuiltBy';
import { useAppVersion } from '@/lib/useAppVersion';

/**
 * About — app identity + build version + studio attribution. Version and
 * attribution share their source with the drawer footer (`BuiltBy`), so the
 * wordmark, the link target, and the version can't drift between the two
 * places they're shown.
 */
export function AboutPanel() {
  const version = useAppVersion();

  return (
    <Panel title="About">
      <div className="flex items-baseline gap-2">
        <div className="font-mono text-sm tracking-wider text-fg">ORBIS</div>
        <div className="text-helper text-fg-muted tabular-nums">
          {version ? `v${version}` : '—'}
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2 text-helper">
        <ProtoLabsIcon variant="outline" size={16} />
        <ProtoLabsLink />
      </div>
    </Panel>
  );
}

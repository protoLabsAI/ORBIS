import { useEffect, useState } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import { Panel } from '@/components/ui/panel';
import { ProtoLabsIcon } from '@/components/ProtoLabsIcon';

/**
 * About — app identity + build version + studio attribution. The version
 * comes from the Tauri bundle (tauri.conf, bumped by the release
 * automation), so it's the real shipped version, not a frontend constant.
 */
export function AboutPanel() {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => setVersion(null));
  }, []);

  return (
    <Panel title="About">
      <div className="flex items-baseline gap-2">
        <div className="font-mono text-sm tracking-wider text-fg">ORBIS</div>
        <div className="text-helper text-fg-muted">
          {version ? `v${version}` : '—'}
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2 text-helper text-fg-muted">
        <ProtoLabsIcon variant="outline" size={16} />
        <span>by protoLabs.studio</span>
      </div>
    </Panel>
  );
}

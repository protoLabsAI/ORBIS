import { useEffect, useState } from 'react';
import { Settings } from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Slot } from '@/plugins/PluginHost';
import { OrbPreview } from '@/plugins/orb/OrbPreview';
import { useIsMobile } from '@/lib/useMediaQuery';
import { isAdmin, useWhoami } from '@/auth/useWhoami';
import { cn } from '@/lib/utils';

const STORAGE_TAB = 'protoVoice.tab';
type TabName = 'voice' | 'orb';

export function Drawer() {
  const isMobile = useIsMobile();
  const whoami = useWhoami();
  // Orb settings are admin-only — regular users see just the Voice tab.
  // Their orb viz is pinned by the admin; there's nothing to edit.
  const canEditOrb = isAdmin(whoami);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabName>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_TAB);
      if (saved === 'voice' || saved === 'orb') return saved;
    } catch {}
    return 'voice';
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_TAB, tab); } catch {}
  }, [tab]);

  // If a non-admin lands on the persisted "orb" tab, snap them back to voice.
  useEffect(() => {
    if (!canEditOrb && tab === 'orb') setTab('voice');
  }, [canEditOrb, tab]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <button
          type="button"
          aria-label="Open settings drawer"
          className="fixed z-20 grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-zinc-500/60 hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-600 transition-colors"
          style={{
            top: 'calc(0.75rem + env(safe-area-inset-top, 0px))',
            right: 'calc(0.75rem + env(safe-area-inset-right, 0px))',
          }}
        >
          <Settings className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className={cn(
          'flex flex-col',
          isMobile
            ? 'w-full max-w-full gap-0 p-0'
            : 'w-[400px] max-w-[92vw] gap-4',
        )}
        style={{
          paddingTop: 'env(safe-area-inset-top, 0px)',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          paddingRight: isMobile ? undefined : 'env(safe-area-inset-right, 0px)',
        }}
      >
        {/* Title stays present for a11y; only visible on desktop where
            there's vertical budget for it. Mobile hides it to give the
            preview the full top-half of the viewport. */}
        <SheetHeader className={cn('pb-0', isMobile && 'sr-only')}>
          <SheetTitle className="font-mono text-sm tracking-wider uppercase text-zinc-400">
            protoVoice
          </SheetTitle>
          <SheetDescription className="sr-only">
            Voice agent settings and orb visualizer controls.
          </SheetDescription>
        </SheetHeader>

        {/* Mobile: live orb preview in the top half. On desktop the main
            orb is visible behind the drawer, no preview needed. */}
        {isMobile && open && (
          <div className="relative shrink-0 h-[50dvh] bg-[#0a0a0a] border-b border-zinc-800">
            <OrbPreview />
          </div>
        )}

        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as TabName)}
          className={cn(
            'flex-1 min-h-0 flex flex-col',
            isMobile ? 'px-4 pt-3' : 'px-4',
          )}
        >
          <TabsList
            className={cn('grid w-full', canEditOrb ? 'grid-cols-2' : 'grid-cols-1')}
          >
            <TabsTrigger value="voice">Voice</TabsTrigger>
            {canEditOrb && <TabsTrigger value="orb">Orb</TabsTrigger>}
          </TabsList>
          <TabsContent value="voice" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-voice" />
          </TabsContent>
          {canEditOrb && (
            <TabsContent value="orb" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
              <Slot name="drawer-orb" />
            </TabsContent>
          )}
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

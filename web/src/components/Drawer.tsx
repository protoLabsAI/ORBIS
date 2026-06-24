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
import { useDevMode } from '@/shared/devMode';
import { cn } from '@/lib/utils';

const STORAGE_TAB = 'orbis.tab';
// The orb editor is free + open source (no paywall). The orb-settings plugin
// renders into the 'drawer-orb' slot via the Orb tab below.
type TabName = 'quick' | 'voice' | 'brain' | 'orb' | 'settings' | 'dev';
const ALL_TABS: readonly TabName[] = ['quick', 'voice', 'brain', 'orb', 'settings', 'dev'];
const isTabName = (value: string): value is TabName =>
  (ALL_TABS as readonly string[]).includes(value);

export function Drawer() {
  const isMobile = useIsMobile();
  const devMode = useDevMode();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabName>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_TAB);
      if (saved && isTabName(saved)) return saved;
    } catch {
      // localStorage can be unavailable in restricted webviews.
    }
    return 'quick';
  });
  // Dev is dev-gated; fall back to Quick when developer mode is off so a
  // persisted pick can't strand the drawer.
  const effectiveTab: TabName = !devMode && tab === 'dev' ? 'quick' : tab;

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_TAB, effectiveTab);
    } catch {
      // localStorage can be unavailable in restricted webviews.
    }
  }, [effectiveTab]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <button
          type="button"
          aria-label="Open settings drawer"
          className="fixed z-20 grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint transition-colors"
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
          <SheetTitle className="font-mono text-sm tracking-wider uppercase text-fg-muted">
            ORBIS
          </SheetTitle>
          <SheetDescription className="sr-only">
            Voice agent settings and orb visualizer controls.
          </SheetDescription>
        </SheetHeader>

        {/* Mobile: live orb preview in the top half. On desktop the main
            orb is visible behind the drawer, no preview needed. */}
        {isMobile && open && (
          <div className="relative shrink-0 h-[50dvh] bg-surface border-b border-edge">
            <OrbPreview />
          </div>
        )}

        <Tabs
          value={effectiveTab}
          onValueChange={(v) => setTab(v as TabName)}
          className={cn(
            'flex-1 min-h-0 flex flex-col',
            isMobile ? 'px-4 pt-3' : 'px-4',
          )}
        >
          <TabsList className={cn('grid w-full', devMode ? 'grid-cols-6' : 'grid-cols-5')}>
            <TabsTrigger value="quick">Quick</TabsTrigger>
            <TabsTrigger value="voice">Voice</TabsTrigger>
            <TabsTrigger value="brain">Brain</TabsTrigger>
            <TabsTrigger value="orb">Orb</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            {devMode && <TabsTrigger value="dev">Dev</TabsTrigger>}
          </TabsList>
          <TabsContent value="quick" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-quick" />
          </TabsContent>
          <TabsContent value="voice" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-voice" />
          </TabsContent>
          <TabsContent value="brain" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-brain" />
          </TabsContent>
          <TabsContent value="orb" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-orb" />
          </TabsContent>
          <TabsContent value="settings" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
            <Slot name="drawer-settings" />
          </TabsContent>
          {devMode && (
            <TabsContent value="dev" className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6 space-y-4">
              <Slot name="drawer-dev" />
            </TabsContent>
          )}
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

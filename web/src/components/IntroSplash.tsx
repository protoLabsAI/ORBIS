import { Splash } from '@protolabsai/ui/splash';
import { ProtoLabsIcon } from './ProtoLabsIcon';

/**
 * protoLabs.studio brand bumper — shown over everything on launch, ahead
 * of the ORBIS loading screen.
 *
 * The hand-rolled implementation moved into the design system as
 * `@protolabsai/ui`'s `Splash` (extracted from this very component +
 * protoAgent's). This is now a thin brand-binding: ORBIS's mark + the
 * wordmark + the original timings; entrance/glow/gradient/fade all come
 * from the DS (`--pl-*` tokens, pinned dark via data-theme on <html>).
 */

const HOLD_MS = 1500; // entrance + hold before fade-out begins
const FADE_MS = 550; // fade-out duration

export function IntroSplash() {
  return (
    <Splash
      logo={<ProtoLabsIcon variant="outline" size={88} />}
      word="protoLabs.studio"
      holdMs={HOLD_MS}
      fadeMs={FADE_MS}
    />
  );
}

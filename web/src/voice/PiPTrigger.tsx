import { useState } from 'react';
import { PictureInPicture } from 'lucide-react';
import { useVoiceStateSelector } from './hooks';
import { isPiPSupported } from './usePipSupport';
import { PiPHost } from './PiPHost';

/**
 * Compact "pop out" button rendered top-right while a voice session
 * is connected. Click to open the floating PiP overlay; click again
 * (or close the floating window via its native X) to dismiss.
 *
 * Hidden on browsers without Document PiP support — Safari + Firefox
 * don't ship the API yet, and a button that does nothing when clicked
 * is worse than no button at all.
 *
 * Hidden when disconnected so it doesn't clutter the idle-orb canvas.
 * The trigger only makes sense once there's an active voice loop to
 * keep visible from another window.
 */
export function PiPTrigger() {
  const [open, setOpen] = useState(false);
  const connected = useVoiceStateSelector((s) => s.connected);

  if (!isPiPSupported || !connected) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={open ? 'Close floating overlay' : 'Float over other windows'}
        aria-label={open ? 'Close floating overlay' : 'Open floating overlay'}
        className="pointer-events-auto fixed right-4 top-4 z-30 rounded-md border border-zinc-800 bg-zinc-950/70 backdrop-blur p-2 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
      >
        <PictureInPicture className="h-4 w-4" />
      </button>
      <PiPHost open={open} onClose={() => setOpen(false)} />
    </>
  );
}

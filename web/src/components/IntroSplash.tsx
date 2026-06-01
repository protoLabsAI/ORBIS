import { useEffect, useState } from 'react';
import { ProtoLabsIcon } from './ProtoLabsIcon';

/**
 * protoLabs.studio brand bumper — a brief splash shown over everything on
 * launch, ahead of the ORBIS loading screen. Plays an entrance, holds, then
 * fades out and unmounts to reveal whatever's underneath (BootStatus while
 * models load, or the app if it's already ready).
 *
 * Brand rules (protoContent docs/reference/visual-identity.md): the wordmark
 * is `protoLabs.studio` (lowercase p, capital L, the dot is part of the
 * mark); the brand gradient is for hero text fills / brand moments; the icon
 * background is the brand violet #7c3aed; subtle violet glow only.
 */

const HOLD_MS = 1500; // entrance + hold before fade-out begins
const FADE_MS = 550; // fade-out duration

export function IntroSplash() {
  const [fading, setFading] = useState(false);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const t1 = window.setTimeout(() => setFading(true), HOLD_MS);
    const t2 = window.setTimeout(() => setGone(true), HOLD_MS + FADE_MS);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, []);

  if (gone) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-6 bg-surface transition-opacity ease-out"
      style={{
        transitionDuration: `${FADE_MS}ms`,
        opacity: fading ? 0 : 1,
      }}
      aria-label="protoLabs.studio"
    >
      <div className="pl-rise flex flex-col items-center gap-6">
        {/* Outline mark per brand rules (protoContent visual-identity: the
            in-app / favicon treatment is the face-only outline, paired with
            the wordmark as text). Subtle glow on the strokes, not a filled
            square. */}
        <div style={{ filter: 'drop-shadow(0 0 14px rgba(167, 139, 250, 0.35))' }}>
          <ProtoLabsIcon variant="outline" size={88} />
        </div>
        <div
          className="font-sans text-3xl font-semibold tracking-tight bg-clip-text text-transparent"
          style={{ backgroundImage: 'var(--brand-gradient)' }}
        >
          protoLabs.studio
        </div>
      </div>

      <style>{`
        @keyframes pl-rise {
          0%   { opacity: 0; transform: translateY(10px) scale(0.985); }
          100% { opacity: 1; transform: none; }
        }
        .pl-rise { animation: pl-rise 0.7s cubic-bezier(.2,.6,.2,1) both; }
      `}</style>
    </div>
  );
}

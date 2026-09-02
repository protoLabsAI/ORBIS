import { Mic, MicOff } from 'lucide-react';
import type { VoiceLifecycle } from './lifecycle';

export function VoiceMicButton({
  ready,
  muted,
  unavailableText,
  onClick,
}: {
  ready: boolean;
  muted: boolean;
  unavailableText: string;
  onClick: () => void;
}) {
  const label = ready
    ? muted ? 'Unmute microphone' : 'Mute microphone'
    : unavailableText;
  return (
    <>
      <button
        type="button"
        onClick={onClick}
        disabled={!ready}
        aria-label={label}
        aria-describedby={!ready ? 'voice-mic-availability' : undefined}
        aria-pressed={muted}
        title={ready
          ? muted ? 'Muted — click to unmute' : 'Mic live — click to mute'
          : unavailableText}
        className="relative grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint disabled:cursor-not-allowed disabled:opacity-40 transition-colors"
      >
        {!ready || muted ? (
          <MicOff className="h-[18px] w-[18px]" strokeWidth={1.5} />
        ) : (
          <Mic className="h-[18px] w-[18px]" strokeWidth={1.5} />
        )}
      </button>
      <span
        id="voice-mic-availability"
        className="sr-only"
      >
        {!ready ? unavailableText : ''}
      </span>
    </>
  );
}

export function VoiceRecoveryNotice({
  lifecycle,
  busy,
  onRetry,
  onRelaunch,
}: {
  lifecycle: VoiceLifecycle | null;
  busy: boolean;
  onRetry: () => void;
  onRelaunch: () => void;
}) {
  if (lifecycle?.state !== 'failed') return null;

  return (
    <div
      className="flex items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger/5 p-3"
      role="alert"
      aria-atomic="true"
    >
      <span className="min-w-0 text-helper text-danger">
        {lifecycle.detail || 'Voice did not start.'}
      </span>
      {lifecycle.action === 'retry' && (
        <button
          type="button"
          disabled={busy}
          onClick={onRetry}
          className="h-7 shrink-0 rounded-lg bg-secondary px-2.5 text-[0.8rem] font-medium text-secondary-foreground disabled:opacity-50"
        >
          {busy ? 'Retrying…' : 'Retry voice'}
        </button>
      )}
      {lifecycle.action === 'relaunch_required' && (
        <button
          type="button"
          disabled={busy}
          onClick={onRelaunch}
          className="h-7 shrink-0 rounded-lg bg-secondary px-2.5 text-[0.8rem] font-medium text-secondary-foreground disabled:opacity-50"
        >
          {busy ? 'Relaunching…' : 'Relaunch ORBIS'}
        </button>
      )}
    </div>
  );
}

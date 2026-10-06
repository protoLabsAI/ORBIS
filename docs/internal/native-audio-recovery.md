# Native audio recovery — promotion readiness

The native socket serves one Python client at a time and accepts another after
EOF, a write failure, or a one-second socket write timeout. The microphone
receiver and wake detector belong to the engine lifetime, rather than the first
connection. Microphone audio is drained while offline and queued frames are
cleared before accepting a new session. Disconnect closes the listening gate
and flushes pending playback. Reconnect sends the current hardware-AEC mode
again; it does not reopen a listening turn.

`audio_status` and `orbis-audio-status` expose retained Rust truth:
`socket_connected`, `capture_alive`, `detail`, and `relaunch_required`.
`capture_alive` means complete microphone frames reached the socket pump within
the five-second liveness threshold, checked every second. Socket writes are
bounded to one second; polling and task scheduling may delay the warning.
Silence, hard mute, push-to-talk idle, and the half-duplex echo
gate still produce capture frames and do not trip this watchdog. A stream that
stops delivering frames closes the listening gate, flushes playback, and offers
Relaunch ORBIS. Returning callbacks clear capture failure without reopening a
turn. The native listening command refuses activation when socket/capture
health is unavailable.

This is separate from Pipecat's retained `voice-lifecycle`: a working socket or
SSE stream alone does not establish a ready pipeline. Python peer loss and
speaker-write failure invalidate that lifecycle. Reader EOF cleanup does not
cancel or await the reader task itself. The frontend combines native health
with pipeline readiness and reads retained SSE/native state after registering
listeners. SSE loss clears stale speaking/tool indicators while preserving the
native listening gate; backend or capture loss clears listening truth.

A sidecar exit after `ORBIS_READY`, including exit code zero, is unexpected
unless app shutdown has been requested. The supervisor invalidates the backend
URL, clears connection truth, closes listening/playback, and presents relaunch
guidance. Normal app quit stays silent. There is no automatic sidecar respawn,
pipeline hot-swap, or hardware-stream reopening: those require a safe session,
tool ownership, and device lifecycle contract.

## Device gate before promotion

Automated tests use real Unix sockets and fake audio endpoints; they do not
open a microphone or validate CoreAudio recovery. Keep the change draft until
Apple Silicon testing records:

- Fresh permission grant and returning-user launch reach callback, socket, and
  Pipecat readiness. Silence and hard mute stay healthy during a long soak.
- A sidecar loss while idle, speaking, and delegating clears stale UI and
  playback, presents relaunch guidance, and recovers after relaunch. Normal
  quit shows no crash dialog and leaves no process tree behind.
- A socket-only client disconnect/reconnect resumes fresh mic/playback data
  without buffered speech or duplicate wake detectors.
- Input/output device changes, sleep/wake, and a long conversation either keep
  capture alive or show the failure promptly after the five-second threshold; relaunch restores it.
- Wake activation, manual listening, hard mute, echo handling, and barge-in
  still work after integration with the separate wake-word restoration change.

This removes the single-shot accept defect in #486 and detects the no-frame
symptom in #602. It does not establish the underlying cause of #602 or prove
that a stalled hardware stream can be restarted safely in place. Cross-backend
persona pipeline hot-swap remains separate work.

# Voice isn't working

A checklist for when ORBIS won't hear you, won't talk back, or both. Work top
to bottom — each step rules out a layer of [the voice loop](/explanation/the-voice-loop).

## ORBIS doesn't hear me

**1. Watch the level meter.** Open **Settings → Voice → Microphone** and speak.
If the meter moves, your mic is reaching ORBIS — skip to
[*it hears me but doesn't reply*](#orbis-hears-me-but-doesn-t-reply). If it
doesn't move, continue.

**2. Check mic permission.** In the same panel, confirm microphone access is
granted. If it shows as denied, click through to **System Settings → Privacy &
Security → Microphone** and enable ORBIS, then relaunch.

**3. Check the input device.** If a device selector is shown, make sure it's the
mic you're actually speaking into (not, say, a disconnected headset). If there's
no selector, ORBIS is following your **macOS system input** — set the right
input in **System Settings → Sound → Input**.

**4. Make sure it's listening.** ORBIS only transcribes when it's in the
listening state. Double-click the orb to start a turn, and watch the orb change
to **listening**. It waits for a natural pause before it transcribes, so give it
a beat after you stop talking.

## ORBIS hears me but doesn't reply

If the transcript appears (or the level meter moved) but the orb never speaks:

**1. Check the language model.** No reply usually means the LLM isn't reachable.
Open **Settings → Agent → LLM** and confirm the endpoint and key. See the
[LLM reference](/reference/agent).

**2. Check the TTS backend.** If you switched off the default (`kokoro`) to a
hosted voice, a wrong URL/key produces no audio. Switch back to **kokoro**
(local, no network) to isolate the problem — see
[TTS](/reference/voice#text-to-speech-tts).

**3. Check output volume / device.** Confirm your Mac's output isn't muted and
is routed to the speakers you expect.

## The orb interrupts itself, or hears its own voice

This is usually echo cancellation being defeated by a loud, close speaker.
Lower the output volume, move the mic away from the speakers, or use headphones.

## Still stuck? Restart clean

ORBIS keeps running after its window closes, so a real restart is worth it:

1. Use **⌘Q**, **Dock → Quit**, or **menu-bar orb → Quit ORBIS** (this fully
   exits, not just hides).
2. Relaunch ORBIS.

A clean restart re-acquires the mic and rebuilds the audio pipeline, which
clears most transient glitches.

## See also

- [Voice reference](/reference/voice)
- [The voice loop](/explanation/the-voice-loop)

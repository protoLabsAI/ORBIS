# Enable wake-word activation

Wake word is an opt-in activation mode in the native Mac app. **Tap to talk**
remains the default; double-clicking the orb also works in wake-word mode.

1. Open **Settings → Voice → Activation → Wake words & tuning**.
2. Download a phrase. ORBIS downloads its two shared models automatically,
   verifies all three files, and selects the downloaded phrase.
3. Choose **Wake word** as the activation style, then relaunch ORBIS.
4. Wait for the status pill to show the phrase. Say it to open a listening
   window. After the configured quiet period, ORBIS returns to waiting for
   the phrase. Thinking, delegated work, and playback hold the window open.

The fresh phrase preference is **Hey Jarvis**, a stock openWakeWord model.
Stock wake classifiers carry the upstream
[CC BY-NC-SA 4.0 license](https://github.com/dscripka/openWakeWord#license);
the picker links each model's source and license.
**Hey Orbis** is available as an experimental custom phrase. Its published
[model card](https://huggingface.co/protoLabsAI/hey-orbis-wakeword) describes
synthetic evaluations; we still need real microphone positive clips and hard
negatives before claiming dependable recognition in ORBIS. Every phrase needs
spoken QA on the native app before promotion.

The microphone mute button overrides wake detection. Muting closes the window
and invalidates buffered wake audio; saying the phrase while muted cannot open
it. Detection runs on-device. Audio reaches the configured speech pipeline only
when the listening window is open (including a manual double-click).

If downloads fail, retry the download. A corrupt file is never considered
installed. Wake-word mode stays unavailable until the **selected** phrase and
both shared models verify. Quick-panel activation uses the same requirement.

If the status says **wake word unavailable**, use double-click to talk and open
Voice settings to download the selected model again. Changes to phrase,
activation style, sensitivity, or listen window apply on the next launch.
Removing the selected phrase switches the next launch to Tap to talk.

Lowering the sensitivity threshold makes activation easier and can increase
false triggers. Test the phrase at your normal distance, with playback and
background speech, before settling on a threshold. Timer recognizes specific
minute/hour timer phrases rather than a generic “set a timer”.

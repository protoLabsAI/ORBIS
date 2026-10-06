# Train your own wake word

ORBIS detects wake words with [openWakeWord](https://github.com/dscripka/openWakeWord) —
small, on-device models that run before the main speech pipeline. The picker in
**Settings → Voice → Activation** ships "Hey Orbis" plus the stock openWakeWord set, but
you can train a model for **any** phrase you like ("Hey Computer", your own
name, …) and integrate it into a development build.

A wake model is tiny (~200 KB–1.5 MB) and trains on **synthetic** speech, so you
don't record anything yourself — a free Colab GPU does it in under an hour.

## 1. Train the model

openWakeWord ships an automatic training pipeline. The easiest path is its Colab
notebook:

1. Open **[openWakeWord → automatic model training](https://github.com/dscripka/openWakeWord#training-new-models)**
   (the `automatic_model_training.ipynb` notebook — "Open in Colab").
2. Set your **target phrase** (e.g. `hey computer`). Keep it 2–4 syllables and
   distinct from everyday speech — short or common phrases false-fire.
3. Run the cells. It synthesizes thousands of positive samples (via Piper TTS)
   plus hard negatives, trains a small classifier, and exports an **`.onnx`**.
4. Download `your_phrase.onnx` when it finishes (~30–60 min on the free GPU).

> Want the exact recipe we used for "Hey Orbis"? It's at
> [`protoLabsAI/hey-orbis-wakeword`](https://huggingface.co/protoLabsAI/hey-orbis-wakeword).

## 2. Integrate it into ORBIS

The shipped picker accepts the pinned built-in catalog. Dropping an arbitrary
file into the models directory does **not** register it or make it selectable.
Custom model import is not currently an app feature.

For a development build, add an entry to `voice/wakeword_catalog.json` with the
classifier's filename, immutable download URL, SHA256, input embedding window
and output scoring offset. Python downloads and Rust inference read this same
manifest. Keep the shared mel-spectrogram and speech-embedding dependencies.
Verify the input/output shape and Rust/Python score parity before exposing it
in the picker, then run spoken positives and hard negatives to tune its
threshold. Build the native app with the updated catalog.

## 3. Select + tune it

Follow [Enable wake-word activation](./enable-wake-word.md): download and select
the catalog entry, enable Wake word, and relaunch. Sensitivity changes also
apply on the next launch. Lower thresholds can increase false triggers.

## Troubleshooting

- **Never fires.** First check your **mic input level** — System Settings →
  Sound → Input → raise *Input volume*. openWakeWord needs a real signal; a
  near-silent mic (the meter barely moving) won't trigger it no matter the
  threshold. See [Voice isn't working](/how-to/voice-not-working).
- **False fires.** Raise the sensitivity threshold, or pick a longer / more
  distinctive phrase and retrain.
- **Won't load.** Make sure both shared models are present in
  `~/Library/Application Support/studio.protolabs.orbis/models/wakeword/`, the catalog checksum matches, and the classifier's input/output
  shapes agree with its catalog entry.

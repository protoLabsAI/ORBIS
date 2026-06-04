# Train your own wake word

ORBIS detects wake words with [openWakeWord](https://github.com/dscripka/openWakeWord) —
small, on-device models that run before the main speech pipeline. The picker in
**Settings → Activation** ships "Hey Orbis" plus the stock openWakeWord set, but
you can train a model for **any** phrase you like ("Hey Computer", your own
name, …) and drop it in.

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

## 2. Install it in ORBIS

Your model rides on two **shared** front-end models (mel-spectrogram +
embedding) that ORBIS already downloads for any wake word — you only add your
classifier. Drop the file into the wake-word models directory:

```
~/Library/Application Support/studio.protolabs.orbis/models/wakeword/
```

Put `your_phrase.onnx` there next to `melspectrogram.onnx` and
`embedding_model.onnx` (download any built-in wake word once in Settings →
Activation to fetch those two if you haven't). The file's name (minus `.onnx`)
is its id, and ORBIS turns it into a display phrase — `hey_computer.onnx` →
"Hey Computer".

## 3. Select + tune it

1. **Settings → Activation** → choose the **Wake word** style and select your
   model from the list.
2. **Relaunch** — activation settings apply on the next launch.
3. Say your phrase. The orb's status pill shows it while armed and flips to
   listening when it fires.
4. If it's unreliable, tune the **Sensitivity** slider (lower = fires more
   easily) and **relaunch**. A clean utterance should score far above a
   background of ~0.

## Troubleshooting

- **Never fires.** First check your **mic input level** — System Settings →
  Sound → Input → raise *Input volume*. openWakeWord needs a real signal; a
  near-silent mic (the meter barely moving) won't trigger it no matter the
  threshold. See [Voice isn't working](/how-to/voice-not-working).
- **False fires.** Raise the sensitivity threshold, or pick a longer / more
  distinctive phrase and retrain.
- **Won't load.** Make sure both shared models are present in the directory
  above and the file is a valid openWakeWord `.onnx` (input `[1,16,96]`).

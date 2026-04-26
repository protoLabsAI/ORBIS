# Training a custom wake word for ORBIS

This guide walks through training a "hey orbis" (or any custom phrase) model
using the openWakeWord automated training notebook. The entire process takes
about an hour on a free Google Colab T4 GPU and requires no ML experience.

---

## Prerequisites

- A Google account (for Colab)
- The wake-word extra installed locally to test the finished model:
  ```bash
  pip install -e ".[wake-word]"
  ```

---

## Steps

### 1. Open the training notebook

Open the automated training notebook in Google Colab:
[openWakeWord — automatic_model_training.ipynb](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb)

Click **"Open in Colab"** and make sure the runtime is set to **GPU**
(Runtime → Change runtime type → T4 GPU).

### 2. Configure the target phrase

In the notebook's configuration cell, set:

```python
TARGET_PHRASE = "hey orbis"   # the phrase you want to detect
```

The notebook will:
1. Generate thousands of synthetic audio clips of "hey orbis" using open-source
   TTS models (no recording required).
2. Mix in ~30,000 hours of negative data (speech, noise, music) so the model
   has a low false-accept rate.
3. Train a small classifier head on top of the frozen openWakeWord backbone.

### 3. Export the model

At the end of the notebook, export as **TFLite** (recommended — better CPU
efficiency on x86 and ARM64):

```python
EXPORT_FORMAT = "tflite"   # or "onnx" for Windows deployments
```

Download the resulting `hey_orbis.tflite` file.

### 4. Place the model in ORBIS

```bash
mkdir -p data/wake_word
mv ~/Downloads/hey_orbis.tflite data/wake_word/hey_orbis.tflite
```

### 5. Configure ORBIS to use it

**Option A — env var:**
```bash
# .env
WAKE_WORD_MODEL=data/wake_word/hey_orbis.tflite
WAKE_WORD_THRESHOLD=0.5   # tune up if too many false positives
WAKE_WORD_TIMEOUT=30
```

**Option B — config file:**
```yaml
# config/orbis.yaml
persona:
  behavior:
    wake_word:
      enabled: true
      model_path: data/wake_word/hey_orbis.tflite
      threshold: 0.5
      timeout: 30
```

### 6. Test it

```bash
python app.py
```

Speak "hey orbis" — the orb should wake. Check the logs for:
```
[wake_word] loading custom model: data/wake_word/hey_orbis.tflite
[wake_word] detected 'hey_orbis' (score=0.87)
```

---

## Tuning the threshold

- **Too many false positives** (wakes on background speech): increase
  `WAKE_WORD_THRESHOLD` toward `0.7`–`0.8`.
- **Missing real activations** (doesn't wake when you say it): decrease
  toward `0.3`–`0.4`, or retrain with more data.

The default `0.5` targets <0.5 false accepts per hour in a quiet room.

---

## Using a pre-trained model (dev/testing)

If you just want to test the wake-word gate without training, use one of the
bundled pre-trained models:

```bash
WAKE_WORD="hey jarvis"   # hey jarvis | hey mycroft | alexa | hey rhasspy
```

These are English-only and tuned for their specific phrases, so false-accept
rates on "hey orbis" will be high — use them for pipeline testing only.

---

## Community models

The Home Assistant community maintains a collection of custom openWakeWord
models at:
https://github.com/fwartner/home-assistant-wakewords-collection

Some may work as-is or serve as a starting point for fine-tuning.

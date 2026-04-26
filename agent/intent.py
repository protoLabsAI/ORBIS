"""Intent classifier — pre-LLM routing for ORBIS turns (#96).

Architecture
------------
all-MiniLM-L6-v2 (88 MB, 384-dim) → frozen embeddings
  → Linear(384, 128) → ReLU → Dropout(0.2) → Linear(128, 5)
  → softmax → (intent, confidence)

5 classes (from DELTA.md v0):
  chat      → LLM, no tools
  command   → direct tool dispatch, no LLM
  delegate  → delegate_to() + LLM
  memory    → retrieval + LLM
  meta      → LLM fallback (ambiguous / multi-intent)

When confidence ≥ INTENT_THRESHOLD (default 0.85):
  - "command" → tools executed directly, LLM skipped
  - "chat"    → LLM called without tool-calling system prompt overhead
  - all other → standard path (LLM + tools)

When confidence < INTENT_THRESHOLD → route as "meta" (LLM fallback).

Model loading
-------------
Priority order:
  1. INTENT_MODEL_PATH env var (absolute or relative path to .pt file)
  2. data/intent/intent_classifier_v0.pt (default local path)
  3. HuggingFace: protoLabsAI/hey-orbis-intent (if HF_TOKEN set or public)

Installation
------------
    pip install -e ".[intent]"

References
----------
- Issue #96
- Lab: protoLabsAI/protoLab / experiments/companion-stack/pipes/text-pre/intent-classifier
- eval/results_v0.json: macro F1 0.806; at 0.85 threshold: 96.4% acc on 65% of turns
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

INTENT_LABELS = ["chat", "command", "delegate", "memory", "meta"]
INTENT_THRESHOLD_DEFAULT = 0.85
_HF_REPO = "protoLabsAI/hey-orbis-intent"
_DEFAULT_LOCAL_PATH = Path("data/intent/intent_classifier_v0.pt")
_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Prediction result
# ---------------------------------------------------------------------------


@dataclass
class IntentResult:
    intent: str       # one of INTENT_LABELS
    confidence: float
    scores: dict[str, float]  # full softmax distribution
    bypassed: bool = False    # True when LLM was skipped


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class IntentClassifier:
    """Loads the intent model and classifies utterances.

    Lazy-loaded on first call — cold start ~250ms (embedding model load).
    Subsequent calls ~10ms CPU.
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold: float = INTENT_THRESHOLD_DEFAULT,
    ) -> None:
        self._model_path = model_path
        self._threshold = threshold
        self._model = None          # torch.nn.Module
        self._embedder = None       # SentenceTransformer
        self._label_names: list[str] = INTENT_LABELS
        self._loaded = False
        self._unavailable = False   # set True after a failed load to avoid retries

    @classmethod
    def from_env(cls) -> "IntentClassifier":
        path = os.environ.get("INTENT_MODEL_PATH") or None
        try:
            threshold = float(os.environ.get("INTENT_THRESHOLD", str(INTENT_THRESHOLD_DEFAULT)))
        except (TypeError, ValueError):
            threshold = INTENT_THRESHOLD_DEFAULT
        return cls(model_path=path, threshold=threshold)

    def classify(self, text: str) -> Optional[IntentResult]:
        """Classify utterance text. Returns None if model unavailable."""
        if self._unavailable:
            return None
        if not self._load():
            return None
        return self._infer(text)

    def enabled(self) -> bool:
        return not self._unavailable

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> bool:
        if self._loaded:
            return True
        try:
            import torch
            import torch.nn as nn
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning(
                "[intent] sentence-transformers or torch not installed. "
                'Run: pip install -e ".[intent]"'
            )
            self._unavailable = True
            return False

        pt_path = self._resolve_model_path()
        if pt_path is None:
            self._unavailable = True
            return False

        try:
            checkpoint = torch.load(pt_path, map_location="cpu", weights_only=True)
        except Exception as e:
            logger.error(f"[intent] failed to load checkpoint {pt_path}: {e}")
            self._unavailable = True
            return False

        label_names = checkpoint.get("label_names", INTENT_LABELS)
        hidden_size = checkpoint.get("hidden_size", 128)
        embedding_dim = checkpoint.get("embedding_dim", 384)
        n_classes = len(label_names)

        # Reconstruct the exact architecture from train.py.
        model = nn.Sequential(
            nn.Linear(embedding_dim, hidden_size),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_size, n_classes),
        )
        model.load_state_dict(checkpoint["model_state_dict"])
        model.eval()

        self._model = model
        self._label_names = label_names
        self._embedder = SentenceTransformer(_EMBEDDING_MODEL)
        self._loaded = True
        logger.info(
            f"[intent] loaded model from {pt_path} "
            f"(classes={label_names}, threshold={self._threshold})"
        )
        return True

    def _resolve_model_path(self) -> Optional[Path]:
        """Try model_path → default local path → HuggingFace."""
        # 1. Explicit path
        if self._model_path:
            p = Path(self._model_path)
            if p.exists():
                return p
            logger.error(f"[intent] INTENT_MODEL_PATH not found: {p}")
            return None

        # 2. Default local path
        if _DEFAULT_LOCAL_PATH.exists():
            return _DEFAULT_LOCAL_PATH

        # 3. HuggingFace
        try:
            from huggingface_hub import hf_hub_download
            logger.info(f"[intent] downloading model from {_HF_REPO}...")
            path = hf_hub_download(
                repo_id=_HF_REPO,
                filename="intent_classifier_v0.pt",
                local_dir=str(_DEFAULT_LOCAL_PATH.parent),
            )
            return Path(path)
        except Exception as e:
            logger.warning(
                f"[intent] HuggingFace download failed ({e}). "
                f"To use intent classifier, place intent_classifier_v0.pt at "
                f"{_DEFAULT_LOCAL_PATH} or set INTENT_MODEL_PATH."
            )
            return None

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _infer(self, text: str) -> IntentResult:
        import torch

        with torch.no_grad():
            emb = self._embedder.encode([text], show_progress_bar=False)
            x = torch.tensor(emb, dtype=torch.float32)
            logits = self._model(x)
            probs = torch.softmax(logits, dim=-1).squeeze().tolist()

        scores = {label: float(p) for label, p in zip(self._label_names, probs)}
        best_intent = max(scores, key=scores.__getitem__)
        best_conf = scores[best_intent]

        # Below threshold → treat as meta (LLM fallback).
        if best_conf < self._threshold:
            intent = "meta"
            confidence = best_conf
        else:
            intent = best_intent
            confidence = best_conf

        logger.debug(
            f"[intent] '{text[:60]}' → {intent} ({confidence:.3f}) "
            f"scores={scores}"
        )
        return IntentResult(intent=intent, confidence=confidence, scores=scores)


# ---------------------------------------------------------------------------
# needs_tools helper — collapses tool-need-predictor into classifier cascade
# ---------------------------------------------------------------------------


def needs_tools(result: IntentResult) -> bool:
    """True when the classified intent requires tools.

    command / delegate / memory → tools needed.
    chat / meta                 → no tools (per DELTA.md: tool-need-predictor
                                  collapses into this cascade, no separate model).
    """
    return result.intent in {"command", "delegate", "memory"}


# ---------------------------------------------------------------------------
# Module-level singleton — one embedder load per process
# ---------------------------------------------------------------------------


_classifier: Optional[IntentClassifier] = None


def get_classifier() -> IntentClassifier:
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier.from_env()
    return _classifier


# ---------------------------------------------------------------------------
# Pipecat FrameProcessor — sits between audio_tags and user_agg in the
# pipeline. Classifies each TranscriptionFrame and:
#   - "command" @ >threshold  → dispatch tool directly, suppress frame
#                               so LLM is never called (bypass logged to _METRICS)
#   - "chat"    @ >threshold  → set llm_skip_tools flag on the frame so
#                               app.py context builder omits tool schema
#   - all other / low-conf   → pass through unchanged (standard LLM path)
# ---------------------------------------------------------------------------


class IntentRouterProcessor(FrameProcessor):
    """Pre-LLM intent router — sits between audio_tags and user_agg.

    Intercepts ``TranscriptionFrame`` on every user turn. Classifies the
    text and either:

    - **command** at ≥ threshold: calls ``command_handler(text, result)``
      (which dispatches the tool directly), then *suppresses* the frame so
      the context aggregator + LLM never see this turn. Logged as bypassed.
    - **chat** at ≥ threshold: tags the frame with ``intent_result`` attr
      so app.py can omit the tool-calling schema for this call.
    - **all other** / low-confidence: passes frame through unchanged;
      standard LLM path handles it.

    Non-TranscriptionFrames pass through immediately.

    Parameters
    ----------
    classifier:
        Loaded ``IntentClassifier`` instance.
    command_handler:
        Async callable ``(text: str, result: IntentResult) -> None`` that
        dispatches a tool call directly and speaks a response. Called only
        for high-confidence ``command`` turns.
    metrics:
        The app-level ``_METRICS`` dict for in-process counters.
    """

    def __init__(
        self,
        classifier: IntentClassifier,
        command_handler: Optional[Callable[..., Any]] = None,
        metrics: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._classifier = classifier
        self._command_handler = command_handler
        self._metrics = metrics if metrics is not None else {}
        self._last_result: Optional[IntentResult] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if not isinstance(frame, TranscriptionFrame) or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        text = frame.text.strip()
        if not text:
            await self.push_frame(frame, direction)
            return

        result = self._classifier.classify(text)
        if result is None:
            # Classifier unavailable — pass through unmodified.
            await self.push_frame(frame, direction)
            return

        self._last_result = result
        self._update_metrics(result)

        if result.intent == "command" and self._command_handler is not None:
            try:
                await self._command_handler(text, result)
            except Exception as e:
                logger.warning(f"[intent] command_handler failed: {e} — falling back to LLM")
                await self.push_frame(frame, direction)
                return
            # Suppress frame — LLM is not called for this turn.
            self._metrics["intent_llm_bypassed"] = (
                self._metrics.get("intent_llm_bypassed", 0) + 1
            )
            logger.info(
                f"[intent] bypassed LLM for command turn "
                f"(conf={result.confidence:.3f})"
            )
            return

        # For all other intents — annotate the frame so downstream processors
        # (e.g. the context builder) can adjust tool schema / prompt weight.
        frame.intent_result = result  # type: ignore[attr-defined]
        await self.push_frame(frame, direction)

    def _update_metrics(self, result: IntentResult) -> None:
        m = self._metrics
        m["intent_turns_total"] = m.get("intent_turns_total", 0) + 1
        by_class = m.setdefault("intent_by_class", {})
        by_class[result.intent] = by_class.get(result.intent, 0) + 1

    @property
    def last_result(self) -> Optional[IntentResult]:
        return self._last_result

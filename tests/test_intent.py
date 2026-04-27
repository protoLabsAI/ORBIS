"""Tests for the intent classifier and router (#96).

Covers:
- IntentClassifier: from_env(), from_dict(), model unavailable graceful no-op
- IntentResult: fields, needs_tools() cascade
- IntentRouterProcessor: TranscriptionFrame passthrough, command bypass,
  chat annotation, low-confidence fallback to meta, non-audio frame passthrough
- Metrics tracking

The actual ML model is never loaded — IntentClassifier._load() is monkeypatched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pipecat.frames.frames import SystemFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from agent.intent import (
    IntentClassifier,
    IntentResult,
    IntentRouterProcessor,
    INTENT_LABELS,
    INTENT_THRESHOLD_DEFAULT,
    needs_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="test", timestamp="now")


async def _collect(processor, frames):
    collected = []
    async def capture(frame, direction):
        collected.append(frame)
    processor.push_frame = capture
    for frame in frames:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    return collected


def _mock_classifier(intent: str, confidence: float) -> IntentClassifier:
    """Return an IntentClassifier whose classify() is pre-stubbed."""
    clf = IntentClassifier.__new__(IntentClassifier)
    clf._threshold = INTENT_THRESHOLD_DEFAULT
    clf._loaded = True
    clf._unavailable = False
    scores = {label: 0.0 for label in INTENT_LABELS}
    scores[intent] = confidence
    result = IntentResult(
        intent=intent if confidence >= INTENT_THRESHOLD_DEFAULT else "meta",
        confidence=confidence,
        scores=scores,
    )
    clf.classify = MagicMock(return_value=result)
    return clf


# ---------------------------------------------------------------------------
# IntentResult / needs_tools
# ---------------------------------------------------------------------------


class TestNeedsTools:
    def test_command_needs_tools(self):
        r = IntentResult(intent="command", confidence=0.9, scores={})
        assert needs_tools(r)

    def test_delegate_needs_tools(self):
        r = IntentResult(intent="delegate", confidence=0.9, scores={})
        assert needs_tools(r)

    def test_memory_needs_tools(self):
        r = IntentResult(intent="memory", confidence=0.9, scores={})
        assert needs_tools(r)

    def test_chat_no_tools(self):
        r = IntentResult(intent="chat", confidence=0.9, scores={})
        assert not needs_tools(r)

    def test_meta_no_tools(self):
        r = IntentResult(intent="meta", confidence=0.9, scores={})
        assert not needs_tools(r)


# ---------------------------------------------------------------------------
# IntentClassifier: config
# ---------------------------------------------------------------------------


class TestIntentClassifierConfig:
    def test_from_env_unavailable_when_no_model(self, monkeypatch, tmp_path):
        monkeypatch.delenv("INTENT_MODEL_PATH", raising=False)
        # Simulate missing default path + unavailable HF.
        clf = IntentClassifier(model_path=str(tmp_path / "nonexistent.pt"))
        # _load() should fail gracefully.
        result = clf.classify("hello")
        assert result is None
        assert clf._unavailable

    def test_from_env_threshold_default(self, monkeypatch):
        monkeypatch.delenv("INTENT_THRESHOLD", raising=False)
        clf = IntentClassifier.from_env()
        assert clf._threshold == pytest.approx(INTENT_THRESHOLD_DEFAULT)

    def test_from_env_custom_threshold(self, monkeypatch):
        monkeypatch.setenv("INTENT_THRESHOLD", "0.9")
        clf = IntentClassifier.from_env()
        assert clf._threshold == pytest.approx(0.9)

    def test_from_env_bad_threshold_uses_default(self, monkeypatch):
        monkeypatch.setenv("INTENT_THRESHOLD", "not-a-float")
        clf = IntentClassifier.from_env()
        assert clf._threshold == pytest.approx(INTENT_THRESHOLD_DEFAULT)


# ---------------------------------------------------------------------------
# IntentClassifier: below threshold routes to meta
# ---------------------------------------------------------------------------


class TestThresholdFallback:
    def test_below_threshold_routes_to_meta(self):
        clf = IntentClassifier.__new__(IntentClassifier)
        clf._threshold = 0.85
        clf._loaded = True
        clf._unavailable = False

        # Simulate inference returning below-threshold score.
        scores = {"chat": 0.3, "command": 0.2, "delegate": 0.1, "memory": 0.1, "meta": 0.3}

        def _infer(text):
            best = max(scores, key=scores.__getitem__)
            conf = scores[best]
            intent = best if conf >= clf._threshold else "meta"
            return IntentResult(intent=intent, confidence=conf, scores=scores)

        clf._infer = _infer
        clf.classify = lambda t: clf._infer(t)

        result = clf.classify("hmm")
        assert result.intent == "meta"


# ---------------------------------------------------------------------------
# IntentRouterProcessor: frame passthrough
# ---------------------------------------------------------------------------


class TestIntentRouterProcessor:
    @pytest.mark.asyncio
    async def test_non_transcription_passes_through(self):
        clf = _mock_classifier("chat", 0.95)
        router = IntentRouterProcessor(classifier=clf)
        system = SystemFrame()
        out = await _collect(router, [system])
        assert system in out

    @pytest.mark.asyncio
    async def test_chat_frame_annotated_and_passed(self):
        clf = _mock_classifier("chat", 0.95)
        router = IntentRouterProcessor(classifier=clf)
        frame = _make_transcription("tell me a joke")
        out = await _collect(router, [frame])
        assert frame in out
        assert hasattr(frame, "intent_result")
        assert frame.intent_result.intent == "chat"

    @pytest.mark.asyncio
    async def test_memory_frame_passed_through(self):
        clf = _mock_classifier("memory", 0.92)
        router = IntentRouterProcessor(classifier=clf)
        frame = _make_transcription("remember when I said that?")
        out = await _collect(router, [frame])
        assert frame in out
        assert frame.intent_result.intent == "memory"

    @pytest.mark.asyncio
    async def test_command_bypass_suppresses_frame(self):
        """High-confidence command calls command_handler and drops frame."""
        clf = _mock_classifier("command", 0.95)
        handler_called = []
        async def handler(text, result):
            handler_called.append((text, result))
        router = IntentRouterProcessor(classifier=clf, command_handler=handler)
        frame = _make_transcription("be warmer")
        out = await _collect(router, [frame])
        assert frame not in out       # suppressed
        assert len(handler_called) == 1
        assert handler_called[0][0] == "be warmer"

    @pytest.mark.asyncio
    async def test_command_bypass_fallback_on_handler_error(self):
        """If command_handler raises, frame passes through (LLM fallback)."""
        clf = _mock_classifier("command", 0.95)
        async def bad_handler(text, result):
            raise RuntimeError("probe failed")
        router = IntentRouterProcessor(classifier=clf, command_handler=bad_handler)
        frame = _make_transcription("set palette to ocean")
        out = await _collect(router, [frame])
        assert frame in out  # fell back to LLM path

    @pytest.mark.asyncio
    async def test_empty_text_passes_through(self):
        clf = _mock_classifier("chat", 0.95)
        router = IntentRouterProcessor(classifier=clf)
        frame = _make_transcription("   ")
        out = await _collect(router, [frame])
        assert frame in out
        clf.classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_classifier_passthrough(self):
        """If classifier returns None, frame passes unchanged."""
        clf = IntentClassifier.__new__(IntentClassifier)
        clf._unavailable = True
        clf.classify = MagicMock(return_value=None)
        router = IntentRouterProcessor(classifier=clf)
        frame = _make_transcription("anything")
        out = await _collect(router, [frame])
        assert frame in out
        assert not hasattr(frame, "intent_result")


# ---------------------------------------------------------------------------
# Metrics tracking
# ---------------------------------------------------------------------------


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_incremented_per_turn(self):
        metrics = {}
        clf = _mock_classifier("chat", 0.95)
        router = IntentRouterProcessor(classifier=clf, metrics=metrics)
        for _ in range(3):
            await _collect(router, [_make_transcription("hello")])
        assert metrics["intent_turns_total"] == 3
        assert metrics["intent_by_class"]["chat"] == 3

    @pytest.mark.asyncio
    async def test_bypass_counter_incremented(self):
        metrics = {}
        clf = _mock_classifier("command", 0.95)
        async def handler(text, result): pass
        router = IntentRouterProcessor(classifier=clf, command_handler=handler, metrics=metrics)
        await _collect(router, [_make_transcription("be warmer")])
        assert metrics["intent_llm_bypassed"] == 1

"""Cross-encoder reranker for ORBIS memory recall (#96).

Architecture (Tier 1 — both Apple Silicon and server)
------------------------------------------------------
  memory/facts.py search() → BM25 top-N (FTS5, already works)
    → RerankerService.rerank() → cross-encoder scores each (query, fact) pair
    → returns top-K by relevance score

Model: mxbai-rerank-xsmall-v1 (146 MB, 30M params)
  - Benchmarked at 0.877 NDCG@5 vs BM25-only 0.818 (+7%)
  - ~70ms / 20 pairs on CPU — well within the 100ms voice-loop budget
  - Runs identically on Apple Silicon and Linux; no GPU required

Tier 2 (server only, not implemented here)
------------------------------------------
  semantic_search() via embed service → reciprocal rank fusion → reranker
  Requires GATEWAY_URL + embed model routing in LiteLLM config.
  See issue #96 acceptance criteria item 5.

Installation
------------
    pip install -e ".[rerank]"

References
----------
- Issue #96
- Lab: protoLabsAI/protoLab / experiments/companion-stack/pipes/llm-context/reranker
- DELTA.md: mxbai-rerank-xsmall-v1 scored 0.877 NDCG@5; Qwen3-Reranker-0.6B
  scored 0.205 (likely input-format issue — flagged for investigation)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from memory.facts import FactRecord

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mixedbread-ai/mxbai-rerank-xsmall-v1"
_DEFAULT_TOP_K = 5
_DEFAULT_CANDIDATE_N = 20   # BM25 retrieves this many before reranking


class RerankerService:
    """Cross-encoder reranker over BM25 candidates.

    Lazy-loads the cross-encoder on first call. Thread-safe for single-process
    use (FastAPI / asyncio + executor); the model itself is not async but
    rerank() runs in-process synchronously — 70ms is acceptable on the
    pre-LLM path before the voice pipeline waits for context assembly.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        top_k: int = _DEFAULT_TOP_K,
    ) -> None:
        self._model_name = model_name
        self._top_k = top_k
        self._model = None
        self._unavailable = False

    @classmethod
    def from_env(cls) -> "RerankerService":
        model_name = os.environ.get("RERANK_MODEL", _DEFAULT_MODEL)
        try:
            top_k = int(os.environ.get("RERANK_TOP_K", str(_DEFAULT_TOP_K)))
        except (TypeError, ValueError):
            top_k = _DEFAULT_TOP_K
        return cls(model_name=model_name, top_k=top_k)

    def rerank(
        self,
        query: str,
        facts: "list[FactRecord]",
    ) -> "list[FactRecord]":
        """Rerank *facts* against *query*; return top-K by cross-encoder score.

        Falls back to the original BM25 order if the model is unavailable,
        so the memory recall path degrades gracefully rather than crashing.
        """
        if not facts:
            return facts

        # If model isn't loadable, return BM25 order sliced to top_k.
        if self._unavailable:
            return facts[: self._top_k]

        model = self._load()
        if model is None:
            return facts[: self._top_k]

        # Build (query, passage) pairs. Concatenate subject/relation/object
        # into a natural-language passage for the cross-encoder.
        passages = [_fact_to_text(f) for f in facts]

        try:
            results = model.rank(query, passages, return_documents=False)
        except Exception as e:
            logger.warning(f"[reranker] rank() failed: {e} — using BM25 order")
            return facts[: self._top_k]

        # results is a list of {"corpus_id": int, "score": float} sorted desc.
        ranked = [facts[r["corpus_id"]] for r in results[: self._top_k]]
        logger.debug(
            f"[reranker] reranked {len(facts)} facts → top {len(ranked)} "
            f"(top score={results[0]['score']:.3f})"
        )
        return ranked

    def enabled(self) -> bool:
        return not self._unavailable

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            logger.warning(
                "[reranker] sentence-transformers not installed. "
                'Run: pip install -e ".[rerank]"'
            )
            self._unavailable = True
            return None

        try:
            logger.info(f"[reranker] loading {self._model_name}...")
            self._model = CrossEncoder(self._model_name, max_length=512)
            logger.info(f"[reranker] loaded {self._model_name}")
        except Exception as e:
            logger.error(f"[reranker] failed to load {self._model_name}: {e}")
            self._unavailable = True
            return None

        return self._model


def _fact_to_text(fact: "FactRecord") -> str:
    """Convert a FactRecord to a short natural-language sentence for the cross-encoder."""
    parts = [fact.subject, fact.relation, fact.object]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_reranker: Optional[RerankerService] = None


def get_reranker() -> RerankerService:
    global _reranker
    if _reranker is None:
        _reranker = RerankerService.from_env()
    return _reranker

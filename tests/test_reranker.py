"""Tests for memory/reranker.py (#96 Tier 1).

Covers:
- RerankerService: from_env(), graceful fallback when model unavailable
- rerank(): returns top-k, preserves FactRecord shape
- rerank(): falls back to BM25 order when model unavailable
- rerank(): empty input returns empty
- _fact_to_text(): correct concatenation
- facts.py search() integration: rerank=False bypasses reranker
- facts.py search() integration: rerank=True calls reranker when enabled

The actual CrossEncoder is never loaded — RerankerService._model is patched.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from memory.facts import FactRecord, FactsDAL
from memory.reranker import RerankerService, _DEFAULT_TOP_K, _fact_to_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(id_: str, subj: str, rel: str, obj: str) -> FactRecord:
    return FactRecord(
        id=id_, subject=subj, relation=rel, object=obj,
        confidence=1.0, valid_at=None, invalid_at=None,
        created_at="2026-01-01", source_session_id=None,
    )


def _mock_reranker(top_k: int = _DEFAULT_TOP_K) -> RerankerService:
    """RerankerService with a mocked CrossEncoder that reverses input order."""
    svc = RerankerService(top_k=top_k)
    mock_model = MagicMock()
    def fake_rank(query, passages, return_documents=False):
        # Return reversed order as "better" ranking.
        return [{"corpus_id": i, "score": float(len(passages) - i)}
                for i in reversed(range(len(passages)))]
    mock_model.rank.side_effect = fake_rank
    svc._model = mock_model
    return svc


# ---------------------------------------------------------------------------
# _fact_to_text
# ---------------------------------------------------------------------------


class TestFactToText:
    def test_concatenates_fields(self):
        f = _fact("1", "user", "likes", "coffee")
        assert _fact_to_text(f) == "user likes coffee"

    def test_skips_empty_fields(self):
        f = _fact("1", "user", "", "coffee")
        assert _fact_to_text(f) == "user coffee"


# ---------------------------------------------------------------------------
# RerankerService.from_env
# ---------------------------------------------------------------------------


class TestRerankerFromEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        monkeypatch.delenv("RERANK_TOP_K", raising=False)
        svc = RerankerService.from_env()
        assert svc._top_k == _DEFAULT_TOP_K

    def test_custom_top_k(self, monkeypatch):
        monkeypatch.setenv("RERANK_TOP_K", "3")
        svc = RerankerService.from_env()
        assert svc._top_k == 3

    def test_bad_top_k_uses_default(self, monkeypatch):
        monkeypatch.setenv("RERANK_TOP_K", "notanint")
        svc = RerankerService.from_env()
        assert svc._top_k == _DEFAULT_TOP_K


# ---------------------------------------------------------------------------
# RerankerService.rerank
# ---------------------------------------------------------------------------


class TestRerankerRerank:
    def test_empty_input_returns_empty(self):
        svc = _mock_reranker()
        assert svc.rerank("query", []) == []

    def test_reranks_and_slices_top_k(self):
        svc = _mock_reranker(top_k=2)
        facts = [_fact(str(i), f"subject{i}", "rel", "obj") for i in range(5)]
        result = svc.rerank("query", facts)
        assert len(result) == 2
        # fake_rank reverses — fact[4] should be first.
        assert result[0].id == "4"

    def test_fallback_on_rank_error(self):
        svc = RerankerService(top_k=2)
        svc._model = MagicMock()
        svc._model.rank.side_effect = RuntimeError("model error")
        facts = [_fact(str(i), f"s{i}", "r", "o") for i in range(5)]
        result = svc.rerank("query", facts)
        # Falls back to BM25 order sliced to top_k.
        assert len(result) == 2
        assert result[0].id == "0"

    def test_unavailable_returns_bm25_order(self):
        svc = RerankerService(top_k=3)
        svc._unavailable = True
        facts = [_fact(str(i), f"s{i}", "r", "o") for i in range(10)]
        result = svc.rerank("query", facts)
        assert len(result) == 3
        assert result[0].id == "0"

    def test_single_fact_no_rerank_needed(self):
        """Single result returned as-is (no cross-encoder call needed)."""
        svc = _mock_reranker()
        facts = [_fact("1", "user", "likes", "music")]
        result = svc.rerank("query", facts)
        assert len(result) == 1
        assert result[0].id == "1"


# ---------------------------------------------------------------------------
# FactsDAL.search() integration
# ---------------------------------------------------------------------------


def _make_dal():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE facts (
            id TEXT PRIMARY KEY, subject TEXT, relation TEXT, object TEXT,
            confidence REAL, valid_at TEXT, invalid_at TEXT,
            created_at TEXT, expired_at TEXT, source_session_id TEXT,
            last_accessed TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE facts_fts USING fts5(
            subject, relation, object,
            content=facts, content_rowid=rowid
        )
    """)
    conn.commit()
    dal = FactsDAL(conn)
    return dal


class TestFactsSearchWithReranker:
    def test_rerank_false_skips_reranker(self):
        dal = _make_dal()
        dal.add("user", "likes", "coffee")
        dal.add("user", "has_pet", "cat")

        with patch("memory.reranker.get_reranker") as mock_get:
            results = dal.search("coffee", limit=5, rerank=False)
            mock_get.assert_not_called()

        assert len(results) >= 1

    def test_rerank_true_calls_reranker_when_multiple_results(self):
        dal = _make_dal()
        # All share a common token "coffee" so BM25 returns multiple hits.
        for i in range(5):
            dal.add("user", "likes", f"coffee blend {i}")

        mock_svc = MagicMock()
        mock_svc.enabled.return_value = True
        mock_svc.rerank.return_value = [
            FactRecord(
                id="x", subject="user", relation="likes", object="coffee blend 0",
                confidence=1.0, valid_at=None, invalid_at=None,
                created_at="2026-01-01", source_session_id=None,
            )
        ]

        with patch("memory.reranker.get_reranker", return_value=mock_svc):
            results = dal.search("coffee", limit=5, rerank=True)

        mock_svc.rerank.assert_called_once()
        assert results[0].id == "x"

    def test_empty_search_returns_empty_regardless_of_rerank(self):
        dal = _make_dal()
        assert dal.search("", rerank=True) == []
        assert dal.search("", rerank=False) == []

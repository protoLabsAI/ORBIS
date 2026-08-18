"""Tests for agent/reasoning_gate.py — the speak-boundary reasoning
stripper. Drives the pure ReasoningGate core the way streamed LLM chunks
arrive: arbitrary split points, tags fragmented across chunks."""

from agent.reasoning_gate import ReasoningGate


def feed_all(gate: ReasoningGate, chunks: list[str]) -> str:
    out = "".join(gate.feed(c) for c in chunks)
    return out + gate.reset()


def test_clean_text_passes_through():
    gate = ReasoningGate()
    assert feed_all(gate, ["Hello ", "there."]) == "Hello there."
    assert gate.suppressed_chars == 0


def test_whole_block_in_one_chunk():
    gate = ReasoningGate()
    assert feed_all(gate, ["<think>secret plan</think>The answer is 4."]) == \
        "The answer is 4."


def test_block_split_across_chunks():
    gate = ReasoningGate()
    chunks = ["<thi", "nk>let me reas", "on about this</th", "ink>Four."]
    assert feed_all(gate, chunks) == "Four."


def test_tag_split_one_char_per_chunk():
    gate = ReasoningGate()
    chunks = list("<think>hidden</think>ok")
    assert feed_all(gate, chunks) == "ok"


def test_text_before_and_after_block():
    gate = ReasoningGate()
    chunks = ["Sure. <think>", "hmm", "</think> Done."]
    assert feed_all(gate, chunks) == "Sure.  Done."


def test_unclosed_block_suppressed_to_end():
    # Orphan-open: response ends while inside the block — everything after
    # the open tag is reasoning and must not be spoken.
    gate = ReasoningGate()
    assert feed_all(gate, ["Okay. <think>this never clo", "ses"]) == "Okay. "
    assert gate.suppressed_chars == 0  # reset() cleared the counter


def test_scratch_pad_and_thinking_variants():
    gate = ReasoningGate()
    assert feed_all(gate, ["<scratch_pad>x</scratch_pad>a"]) == "a"
    gate = ReasoningGate()
    assert feed_all(gate, ["<thinking>x</thinking>b"]) == "b"
    gate = ReasoningGate()
    assert feed_all(gate, ["<reasoning>x</reasoning>c"]) == "c"


def test_case_insensitive():
    gate = ReasoningGate()
    assert feed_all(gate, ["<Think>x</THINK>y"]) == "y"


def test_less_than_in_math_not_swallowed():
    # "3 < 5" — the "<" is held back one chunk, then flushed once the next
    # chunk shows it isn't a tag.
    gate = ReasoningGate()
    assert feed_all(gate, ["3 <", " 5 is true"]) == "3 < 5 is true"


def test_trailing_lt_flushed_at_response_end():
    gate = ReasoningGate()
    assert feed_all(gate, ["a <"]) == "a <"


def test_multiple_blocks():
    gate = ReasoningGate()
    chunks = ["<think>a</think>one ", "<think>b</think>two"]
    assert feed_all(gate, chunks) == "one two"


def test_suppressed_chars_counted():
    gate = ReasoningGate()
    gate.feed("<think>12345</think>ok")
    assert gate.suppressed_chars == len("<think>") + 5 + len("</think>")


def test_reset_clears_block_state():
    gate = ReasoningGate()
    gate.feed("<think>never closed")
    gate.reset()
    # A fresh response must not be swallowed by stale in-block state.
    assert feed_all(gate, ["hello"]) == "hello"


def test_partial_close_inside_block_held_then_resolved():
    gate = ReasoningGate()
    chunks = ["<think>reasoning</", "think>spoken"]
    assert feed_all(gate, chunks) == "spoken"

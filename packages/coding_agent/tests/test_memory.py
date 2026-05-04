"""Tests for the conversation memory module."""
from __future__ import annotations

from coding_agent.memory import Turn, render_history_as_prompt_prefix


def test_turn_dataclass() -> None:
    """Turn dataclass can be constructed with role and content."""
    turn = Turn(role="user", content="Hello")
    assert turn.role == "user"
    assert turn.content == "Hello"


def test_render_empty_history() -> None:
    """Rendering empty history returns empty string."""
    result = render_history_as_prompt_prefix([])
    assert result == ""


def test_render_single_turn() -> None:
    """Rendering a single turn produces correct prefix."""
    turns = [Turn(role="user", content="write a fizzbuzz")]
    result = render_history_as_prompt_prefix(turns)
    assert "Previous conversation" in result
    assert "User: write a fizzbuzz" in result


def test_render_multiple_turns() -> None:
    """Rendering multiple turns preserves order and roles."""
    turns = [
        Turn(role="user", content="write a fizzbuzz"),
        Turn(role="assistant", content="Here's the code..."),
        Turn(role="user", content="now optimize it"),
    ]
    result = render_history_as_prompt_prefix(turns)
    lines = result.strip().split("\n")
    # Should have header + 3 turn lines + separator
    assert len(lines) >= 4
    assert "User: write a fizzbuzz" in result
    assert "Assistant: Here's the code..." in result
    assert "User: now optimize it" in result
    # Check order
    idx_first = result.index("User: write a fizzbuzz")
    idx_second = result.index("Assistant: Here's the code...")
    idx_third = result.index("User: now optimize it")
    assert idx_first < idx_second < idx_third


def test_fifo_trim_concept() -> None:
    """
    Conceptual test: when storing >20 turns, oldest should be dropped.
    
    This would require DynamoDB mocking (e.g. moto) for a real integration
    test. Here we just verify the Turn structure is compatible with the
    FIFO logic in ConversationStore.
    """
    turns = [Turn(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}") for i in range(25)]
    # If we kept only the last 20:
    trimmed = turns[-20:]
    assert len(trimmed) == 20
    assert trimmed[0].content == "msg 5"  # oldest kept
    assert trimmed[-1].content == "msg 24"  # newest

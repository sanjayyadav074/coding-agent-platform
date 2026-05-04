"""Shared library for the Coding Agent Platform."""
from __future__ import annotations

from .config import Settings, get_settings
from .logging import configure_logging, get_logger
from .memory import ConversationStore, Turn, render_history_as_prompt_prefix
from .models import (
    AgentResult,
    CodingRequest,
    GitHubAction,
    SlackResponse,
)

__all__ = [
    "AgentResult",
    "CodingRequest",
    "ConversationStore",
    "GitHubAction",
    "Settings",
    "SlackResponse",
    "Turn",
    "configure_logging",
    "get_logger",
    "get_settings",
    "render_history_as_prompt_prefix",
]

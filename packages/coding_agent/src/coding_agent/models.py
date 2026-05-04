"""Pydantic models shared across the API and the worker.

Keeping these in a single module guarantees that the data contract between
the FastAPI front door and the Temporal workflow / activities cannot drift.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CodingRequest(BaseModel):
    """Request payload that flows from Slack into the workflow."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(..., description="Unique correlation ID")
    user_id: str = Field(..., description="Slack user ID, e.g. U0123ABC")
    team_id: str = Field(default="", description="Slack workspace ID")
    channel_id: str = Field(default="", description="Slack channel ID")
    text: str = Field(..., min_length=1, max_length=4000)
    response_url: str = Field(..., description="Slack response_url for async replies")
    repo: str | None = Field(default=None, description="Optional GitHub owner/repo")
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class GitHubAction(BaseModel):
    """Structured description of a GitHub side-effect performed by the agent."""

    kind: Literal["read_file", "open_issue", "open_pr", "list_files", "noop"]
    repo: str
    detail: str = ""
    url: str | None = None


class AgentResult(BaseModel):
    """Final output of the workflow, returned to the caller and to Slack."""

    request_id: str
    summary: str
    code: str | None = None
    language: str | None = None
    github_actions: list[GitHubAction] = Field(default_factory=list)
    duration_ms: int = 0


class SlackResponse(BaseModel):
    """Schema accepted by Slack's response_url webhooks."""

    response_type: Literal["ephemeral", "in_channel"] = "ephemeral"
    text: str
    replace_original: bool = False

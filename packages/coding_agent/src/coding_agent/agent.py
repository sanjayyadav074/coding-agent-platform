"""Pydantic AI agent definition.

The agent is built once per worker process so the model client is reused
across requests.  Tool calls are typed and operate against an injected
:class:`~coding_agent.github.GitHubClient`, which means tests and CI can
swap in the mock implementation transparently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from .github import GitHubClient
from .logging import get_logger

log = get_logger("agent")

SYSTEM_PROMPT = """You are a senior software engineer pair-programming with one user over Slack DM.

Rules:
- Be concise and pragmatic. Avoid filler. No preamble like "Sure! Here's...".
- Default to Python unless the user clearly asks for another language.
- Always wrap code in fenced blocks with the correct language tag.
- Use the prior conversation context to resolve pronouns like "it", "this", "that".
  If the user references prior code (e.g. "optimize it"), modify what was previously shown.
- If the request is genuinely ambiguous AND no prior turn provides context, ask one
  short clarifying question instead of inventing details. Never fabricate file paths.
- Only call the GitHub tools (`list_files`, `read_file`, `open_issue`) when the user
  explicitly names a real repository or file. Do NOT call them for generic coding
  questions, do NOT pass placeholder paths like "/path/to/repo".
- Never expose secrets, tokens, or PII in your replies.
"""


@dataclass
class AgentDeps:
    github: GitHubClient
    default_repo: str


def build_agent(model: str) -> Agent[AgentDeps, str]:
    """Construct the Pydantic AI agent with the GitHub tool surface."""

    agent: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        system_prompt=SYSTEM_PROMPT,
        retries=2,
    )

    @agent.tool
    async def list_files(ctx: RunContext[AgentDeps], path: str = "") -> str:
        """List files in the user's default GitHub repository at ``path``."""
        action = await ctx.deps.github.list_files(ctx.deps.default_repo, path)
        return action.detail

    @agent.tool
    async def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
        """Return the contents of ``path`` in the user's default repository."""
        action = await ctx.deps.github.read_file(ctx.deps.default_repo, path)
        return action.detail

    @agent.tool
    async def open_issue(
        ctx: RunContext[AgentDeps], title: str, body: str
    ) -> str:
        """Open a GitHub issue in the user's default repository."""
        action = await ctx.deps.github.open_issue(ctx.deps.default_repo, title, body)
        return action.url or action.detail

    return agent


def extract_output(result: Any) -> str:
    """Best-effort extraction of textual output across pydantic-ai versions."""
    for attr in ("output", "data"):
        value = getattr(result, attr, None)
        if value:
            return str(value)
    return str(result)

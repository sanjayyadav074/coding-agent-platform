from __future__ import annotations

import pytest

from coding_agent.github import MockGitHubClient, build_github_client


@pytest.mark.asyncio
async def test_mock_list_files() -> None:
    client = MockGitHubClient()
    action = await client.list_files("octocat/Hello-World", "src")
    assert action.kind == "list_files"
    assert "src" in action.detail


@pytest.mark.asyncio
async def test_mock_open_issue_returns_url() -> None:
    client = MockGitHubClient()
    action = await client.open_issue("octocat/Hello-World", "Bug", "details")
    assert action.kind == "open_issue"
    assert action.url and action.url.startswith("https://github.com/")


def test_factory_returns_mock_when_no_token() -> None:
    client = build_github_client(token="", use_mock=False)
    assert isinstance(client, MockGitHubClient)


def test_factory_respects_use_mock_flag() -> None:
    client = build_github_client(token="ghp_real", use_mock=True)  # noqa: S106
    assert isinstance(client, MockGitHubClient)

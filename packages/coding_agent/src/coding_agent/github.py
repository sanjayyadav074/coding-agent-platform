"""GitHub integration with a deterministic mock fallback.

If ``GITHUB_TOKEN`` is unset or ``USE_GITHUB_MOCK`` is true the platform uses
a small in-memory mock so that local development, CI and demos never require
network calls or credentials.  In production, the real ``PyGithub`` client is
used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .logging import get_logger
from .models import GitHubAction

log = get_logger("github")


class GitHubClient(Protocol):
    async def list_files(self, repo: str, path: str = "") -> GitHubAction: ...
    async def read_file(self, repo: str, path: str) -> GitHubAction: ...
    async def open_issue(self, repo: str, title: str, body: str) -> GitHubAction: ...


@dataclass
class MockGitHubClient:
    """Returns plausible, deterministic responses without touching the network."""

    async def list_files(self, repo: str, path: str = "") -> GitHubAction:
        log.info("github.mock.list_files", repo=repo, path=path)
        return GitHubAction(
            kind="list_files",
            repo=repo,
            detail=f"[mock] files under {path or '/'}: README.md, src/, tests/",
        )

    async def read_file(self, repo: str, path: str) -> GitHubAction:
        log.info("github.mock.read_file", repo=repo, path=path)
        return GitHubAction(
            kind="read_file",
            repo=repo,
            detail=f"[mock] contents of {path}: '# {path}\\nplaceholder'",
        )

    async def open_issue(self, repo: str, title: str, body: str) -> GitHubAction:
        log.info("github.mock.open_issue", repo=repo, title=title)
        return GitHubAction(
            kind="open_issue",
            repo=repo,
            detail=f"[mock] would open issue '{title}'",
            url=f"https://github.com/{repo}/issues/0",
        )


@dataclass
class RealGitHubClient:
    """Thin async wrapper over PyGithub. Imported lazily to avoid a hard dep."""

    token: str

    def _gh(self):  # pragma: no cover - thin shim, exercised in integration only
        from github import Github  # type: ignore

        return Github(self.token)

    async def list_files(self, repo: str, path: str = "") -> GitHubAction:
        import asyncio

        def _do() -> GitHubAction:
            contents = self._gh().get_repo(repo).get_contents(path or "")
            items = contents if isinstance(contents, list) else [contents]
            names = ", ".join(c.name for c in items)
            return GitHubAction(kind="list_files", repo=repo, detail=names)

        return await asyncio.to_thread(_do)

    async def read_file(self, repo: str, path: str) -> GitHubAction:
        import asyncio

        def _do() -> GitHubAction:
            content = self._gh().get_repo(repo).get_contents(path)
            text = content.decoded_content.decode("utf-8", errors="replace")  # type: ignore[union-attr]
            return GitHubAction(kind="read_file", repo=repo, detail=text[:2000])

        return await asyncio.to_thread(_do)

    async def open_issue(self, repo: str, title: str, body: str) -> GitHubAction:
        import asyncio

        def _do() -> GitHubAction:
            issue = self._gh().get_repo(repo).create_issue(title=title, body=body)
            return GitHubAction(
                kind="open_issue", repo=repo, detail=title, url=issue.html_url
            )

        return await asyncio.to_thread(_do)


def build_github_client(*, token: str, use_mock: bool) -> GitHubClient:
    if use_mock or not token:
        log.info("github.client", impl="mock")
        return MockGitHubClient()
    log.info("github.client", impl="real")
    return RealGitHubClient(token=token)

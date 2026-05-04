from __future__ import annotations

from coding_agent.models import AgentResult, CodingRequest, GitHubAction, SlackResponse


def test_coding_request_validation() -> None:
    req = CodingRequest(
        request_id="abc",
        user_id="U1",
        text="write hello world",
        response_url="https://hooks.slack.com/x",
    )
    assert req.text == "write hello world"


def test_agent_result_roundtrip() -> None:
    result = AgentResult(
        request_id="abc",
        summary="done",
        github_actions=[GitHubAction(kind="noop", repo="o/r")],
        duration_ms=42,
    )
    payload = result.model_dump(mode="json")
    restored = AgentResult.model_validate(payload)
    assert restored == result


def test_slack_response_defaults_to_ephemeral() -> None:
    body = SlackResponse(text="hi")
    assert body.response_type == "ephemeral"

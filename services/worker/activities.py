"""Temporal activities.

Activities encapsulate every side-effect: HTTP calls to Slack, LLM
invocations, and GitHub access.  They are individually retried by Temporal
according to the workflow's retry policy.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from temporalio import activity

from coding_agent import (
    AgentResult,
    CodingRequest,
    ConversationStore,
    SlackResponse,
    Turn,
    get_logger,
    get_settings,
    render_history_as_prompt_prefix,
)
from coding_agent.agent import AgentDeps, build_agent, extract_output
from coding_agent.github import build_github_client

log = get_logger("activity")


_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


async def _post_slack(req: CodingRequest, body: SlackResponse) -> None:
    """Send a message to Slack via response_url *or* chat.postMessage."""
    settings = get_settings()
    payload = body.model_dump()

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        if req.response_url:
            try:
                resp = await client.post(req.response_url, json=payload)
                if resp.status_code >= 400:
                    log.warning(
                        "slack.response_url.failed",
                        status=resp.status_code,
                        request_id=req.request_id,
                    )
            except httpx.HTTPError as e:
                log.warning("slack.response_url.error", error=str(e), request_id=req.request_id)
            return

        token = settings.slack_bot_token.get_secret_value()
        if not token or not req.channel_id:
            log.warning("slack.post.skipped", reason="no token/channel and no response_url")
            return
        try:
            resp = await client.post(
                _SLACK_POST_MESSAGE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"channel": req.channel_id, "text": body.text},
            )
            if resp.status_code >= 400:
                log.warning(
                    "slack.chat_post.failed",
                    status=resp.status_code,
                    request_id=req.request_id,
                )
        except httpx.HTTPError as e:
            log.warning("slack.chat_post.error", error=str(e), request_id=req.request_id)


@activity.defn(name="post_slack_ack")
async def post_slack_ack(payload: dict[str, Any]) -> None:
    req = CodingRequest.model_validate(payload)
    await _post_slack(
        req,
        SlackResponse(
            response_type="ephemeral",
            text=":brain: Thinking…",
        ),
    )


@activity.defn(name="run_agent")
async def run_agent(payload: dict[str, Any]) -> dict[str, Any]:
    req = CodingRequest.model_validate(payload)
    settings = get_settings()
    log.info("agent.run", request_id=req.request_id, user_id=req.user_id)

    started = time.perf_counter()
    activity.heartbeat("starting")

    # Load this user's prior history (private to them — keyed by user_id only)
    prior_turns: list[Turn] = []
    if settings.conversations_table:
        try:
            store = ConversationStore(
                settings.conversations_table, region=settings.aws_region
            )
            prior_turns = await store.load(req.user_id)
        except Exception as e:  # noqa: BLE001 - degrade gracefully
            log.warning("memory.load.failed", error=str(e))

    github = build_github_client(
        token=settings.github_token.get_secret_value(),
        use_mock=settings.use_github_mock,
    )
    agent = build_agent(settings.llm_model)
    deps = AgentDeps(github=github, default_repo=req.repo or settings.github_default_repo)

    history_prefix = render_history_as_prompt_prefix(prior_turns)
    full_prompt = (
        f"{history_prefix}\n\nUser: {req.text}" if history_prefix else req.text
    )

    activity.heartbeat("calling-llm")
    result = await agent.run(full_prompt, deps=deps)
    output = extract_output(result)

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "agent.done",
        request_id=req.request_id,
        duration_ms=duration_ms,
        prior_turns=len(prior_turns),
    )

    return AgentResult(
        request_id=req.request_id,
        summary=output,
        duration_ms=duration_ms,
    ).model_dump(mode="json")


@activity.defn(name="save_history")
async def save_history(payload: dict[str, Any]) -> None:
    """Persist the user/assistant turn pair to DynamoDB."""
    settings = get_settings()
    if not settings.conversations_table:
        return
    user_id = str(payload.get("user_id", ""))
    user_msg = str(payload.get("user_msg", ""))
    assistant_msg = str(payload.get("assistant_msg", ""))
    if not user_id or not assistant_msg:
        return
    try:
        store = ConversationStore(
            settings.conversations_table, region=settings.aws_region
        )
        await store.append(user_id, user_msg, assistant_msg)
    except Exception as e:  # noqa: BLE001 - degrade gracefully
        log.warning("memory.append.failed", error=str(e))


@activity.defn(name="reset_history")
async def reset_history(payload: dict[str, Any]) -> None:
    """Delete all stored history for a user."""
    settings = get_settings()
    if not settings.conversations_table:
        return
    user_id = str(payload.get("user_id", ""))
    if not user_id:
        return
    try:
        store = ConversationStore(
            settings.conversations_table, region=settings.aws_region
        )
        await store.reset(user_id)
    except Exception as e:  # noqa: BLE001
        log.warning("memory.reset.failed", error=str(e))


@activity.defn(name="post_slack_result")
async def post_slack_result(payload: dict[str, Any]) -> None:
    req = CodingRequest.model_validate(payload["request"])
    result = AgentResult.model_validate(payload["result"])
    text = f":robot_face: {result.summary}"
    await _post_slack(req, SlackResponse(response_type="in_channel", text=text))


ALL_ACTIVITIES = [post_slack_ack, run_agent, save_history, reset_history, post_slack_result]

"""Temporal workflow definition.

The workflow is intentionally thin: it validates input, posts an
acknowledgement back to Slack, runs the LLM agent activity with retries, and
posts the final answer.  All non-deterministic work lives in activities so
the workflow stays replay-safe.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from coding_agent.models import AgentResult, CodingRequest


_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
    non_retryable_error_types=["ValueError", "PermissionError"],
)


@workflow.defn(name="CodingWorkflow")
class CodingWorkflow:
    """Coordinates a single Slack-originated coding request."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = CodingRequest.model_validate(payload)
        workflow.logger.info("workflow.start", extra={"request_id": req.request_id})

        # Special command: "/coding reset" wipes the user's conversation history.
        if req.text.strip().lower() in {"reset", "clear", "forget", "/reset"}:
            await workflow.execute_activity(
                "reset_history",
                {"user_id": req.user_id},
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            ack_result = AgentResult(
                request_id=req.request_id,
                summary=":broom: Conversation history cleared. Starting fresh.",
            )
            await workflow.execute_activity(
                "post_slack_result",
                {
                    "request": req.model_dump(mode="json"),
                    "result": ack_result.model_dump(mode="json"),
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            return ack_result.model_dump(mode="json")

        # Best-effort "thinking" indicator. We don't fail the workflow if Slack is flaky.
        await workflow.execute_activity(
            "post_slack_ack",
            req.model_dump(mode="json"),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        result_payload: dict[str, Any] = await workflow.execute_activity(
            "run_agent",
            req.model_dump(mode="json"),
            start_to_close_timeout=timedelta(minutes=2),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )
        result = AgentResult.model_validate(result_payload)

        # Persist the turn pair before posting back to Slack so a Slack
        # outage doesn't lose the user's history. save_history is best-effort.
        await workflow.execute_activity(
            "save_history",
            {
                "user_id": req.user_id,
                "user_msg": req.text,
                "assistant_msg": result.summary,
            },
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.execute_activity(
            "post_slack_result",
            {"request": req.model_dump(mode="json"), "result": result.model_dump(mode="json")},
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        workflow.logger.info("workflow.complete", extra={"request_id": req.request_id})
        return result.model_dump(mode="json")

"""FastAPI front door for the Coding Agent Platform.

Responsibilities:
1. Verify Slack request signatures (HMAC-SHA256) before doing any work.
2. Acknowledge Slack within Slack's 3-second SLA.
3. Hand the request off to a Temporal workflow for durable execution.
4. Expose ``/healthz`` and ``/readyz`` probes plus Prometheus metrics.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from temporalio.client import Client

from coding_agent import (
    CodingRequest,
    Settings,
    configure_logging,
    get_logger,
    get_settings,
)
from coding_agent.slack import SlackSignatureError, verify_slack_signature

REQUESTS_TOTAL = Counter(
    "slack_requests_total", "Slack requests received", ["status"]
)
REQUEST_LATENCY = Histogram(
    "slack_request_seconds", "Slack request handling latency"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, service="coding-agent-api")
    log = get_logger("api")

    state: dict[str, Client | None] = {"temporal": None}

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("api.startup", environment=settings.environment, temporal=settings.temporal_host)
        # Defer Temporal connection so the API can serve liveness probes during
        # cluster startup ordering.
        try:
            state["temporal"] = await Client.connect(
                settings.temporal_host, namespace=settings.temporal_namespace
            )
            log.info("api.temporal.connected")
        except Exception as exc:  # noqa: BLE001 - reported via /readyz
            log.warning("api.temporal.connect_failed", error=str(exc))
        yield
        log.info("api.shutdown")

    app = FastAPI(title="Coding Agent API", version="0.1.0", lifespan=lifespan)

    def get_temporal() -> Client:
        client = state["temporal"]
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Temporal connection not ready",
            )
        return client

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = state["temporal"] is not None
        return JSONResponse(
            {"ready": ready, "temporal": ready},
            status_code=200 if ready else 503,
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/slack/events")
    async def slack_events(
        request: Request,
        x_slack_request_timestamp: Annotated[str | None, Header()] = None,
        x_slack_signature: Annotated[str | None, Header()] = None,
        client: Client = Depends(get_temporal),
    ) -> JSONResponse:
        with REQUEST_LATENCY.time():
            body = await request.body()

            if settings.slack_signature_verification_enabled:
                try:
                    verify_slack_signature(
                        signing_secret=settings.slack_signing_secret.get_secret_value(),
                        request_body=body,
                        timestamp=x_slack_request_timestamp,
                        signature=x_slack_signature,
                    )
                except SlackSignatureError as exc:
                    REQUESTS_TOTAL.labels(status="unauthorized").inc()
                    log.warning("slack.signature.invalid", error=str(exc))
                    raise HTTPException(status_code=401, detail="invalid signature") from exc
            else:
                log.warning("slack.signature.disabled")

            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                payload = await request.json()
                # Slack URL verification handshake
                if payload.get("type") == "url_verification":
                    return JSONResponse({"challenge": payload.get("challenge", "")})
                event = payload.get("event", {})
                user_id = event.get("user", "unknown")
                team_id = payload.get("team_id", "")
                channel_id = event.get("channel", "")
                text = event.get("text", "")
                # Slack Events API has no response_url ─ the worker uses chat.postMessage
                response_url = ""
            else:
                form = dict(await request.form())
                user_id = form.get("user_id", "unknown")
                team_id = form.get("team_id", "")
                channel_id = form.get("channel_id", "")
                text = form.get("text", "")
                response_url = form.get("response_url", "")

            if not text.strip():
                REQUESTS_TOTAL.labels(status="empty").inc()
                return JSONResponse(
                    {"response_type": "ephemeral", "text": "Please include a request."}
                )

            req = CodingRequest(
                request_id=str(uuid.uuid4()),
                user_id=user_id,
                team_id=team_id,
                channel_id=channel_id,
                text=text,
                response_url=response_url,
            )

            workflow_id = f"coding-{req.user_id}-{req.request_id}"
            log.info(
                "slack.request.accepted",
                request_id=req.request_id,
                user_id=req.user_id,
                workflow_id=workflow_id,
            )

            asyncio.create_task(_start_workflow(client, settings, req, workflow_id, log))
            REQUESTS_TOTAL.labels(status="accepted").inc()

            return JSONResponse(
                {
                    "response_type": "ephemeral",
                    "text": ":hourglass_flowing_sand: Working on it…",
                }
            )

    return app


async def _start_workflow(
    client: Client,
    settings: Settings,
    req: CodingRequest,
    workflow_id: str,
    log,
) -> None:
    from coding_agent.models import CodingRequest as _Req  # noqa: F401 (ensure import in worker process too)

    try:
        await client.start_workflow(
            "CodingWorkflow",
            req.model_dump(mode="json"),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        log.info("workflow.started", workflow_id=workflow_id)
    except Exception as exc:  # noqa: BLE001
        log.error("workflow.start_failed", workflow_id=workflow_id, error=str(exc))


app = create_app()

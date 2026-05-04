"""Temporal worker entrypoint."""
from __future__ import annotations

import asyncio
import signal

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from coding_agent import configure_logging, get_logger, get_settings

from activities import ALL_ACTIVITIES
from workflow import CodingWorkflow


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="coding-agent-worker")
    log = get_logger("worker")

    log.info("worker.connect", host=settings.temporal_host, namespace=settings.temporal_namespace)
    client = await Client.connect(
        settings.temporal_host, namespace=settings.temporal_namespace
    )

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[CodingWorkflow],
        activities=ALL_ACTIVITIES,
        max_concurrent_activities=20,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        log.info("worker.signal", action="shutdown")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    log.info("worker.run", task_queue=settings.temporal_task_queue)
    async with worker:
        await stop_event.wait()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())

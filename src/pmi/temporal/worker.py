"""Temporal-Worker für den InitiationWorkflow."""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from pmi.temporal.activities import ACTIVITIES
from pmi.temporal.workflow import InitiationWorkflow


def _host() -> str:
    return os.environ.get("PMI_TEMPORAL_HOST", "localhost:7233")


def _queue() -> str:
    return os.environ.get("PMI_TASK_QUEUE", "pmi-initiation")


async def main() -> None:
    client = await Client.connect(_host())
    async with Worker(
        client,
        task_queue=_queue(),
        workflows=[InitiationWorkflow],
        activities=ACTIVITIES,
    ):
        await asyncio.Future()  # läuft bis zum Prozess-Ende


if __name__ == "__main__":
    asyncio.run(main())

"""Temporal-Worker für den InitiationWorkflow."""
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from pmi.temporal.activities import ACTIVITIES
from pmi.temporal.workflow import InitiationWorkflow


async def main() -> None:
    client = await Client.connect("localhost:7233")
    async with Worker(
        client,
        task_queue="pmi-initiation",
        workflows=[InitiationWorkflow],
        activities=ACTIVITIES,
    ):
        await asyncio.Future()  # läuft bis zum Prozess-Ende


if __name__ == "__main__":
    asyncio.run(main())

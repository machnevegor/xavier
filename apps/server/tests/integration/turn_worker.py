"""A worker process that starts turns and never finishes them, for a test to kill.

Usage: ``python turn_worker.py <temporal address> <namespace> <task queue>``.
"""

import asyncio
import sys
from datetime import timedelta

from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from src.orchestration.models import TurnInput, TurnOutcome


@activity.defn(name="run_turn")
async def run_turn(turn: TurnInput) -> TurnOutcome:
    """Report the turn, then heartbeat until the process is killed."""
    sys.stdout.write(f"running {turn.turn_id}\n")
    sys.stdout.flush()
    while True:
        activity.heartbeat()
        await asyncio.sleep(0.1)


async def main(address: str, namespace: str, task_queue: str) -> None:
    """Serve ``run_turn`` on the task queue until killed."""
    client = await Client.connect(
        address, namespace=namespace, data_converter=pydantic_data_converter
    )
    async with Worker(
        client,
        task_queue=task_queue,
        activities=[run_turn],
        max_heartbeat_throttle_interval=timedelta(milliseconds=200),
    ):
        sys.stdout.write("ready\n")
        sys.stdout.flush()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))

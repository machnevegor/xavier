import asyncio
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from src.orchestration.workflows import ChatWorkflow

HISTORIES = Path(__file__).parents[1] / "fixtures" / "histories" / "chat_workflow"
"""Recorded by ``XAVIER_RECORD_HISTORIES=1`` in the integration replay test."""


@pytest.mark.parametrize("path", sorted(HISTORIES.glob("*.json")), ids=lambda path: path.stem)
async def test_stored_chat_workflow_history_replays(path: Path) -> None:
    """Changes to `ChatWorkflow` must replay stored histories or go behind `workflow.patched`."""
    history = WorkflowHistory.from_json(path.stem, await asyncio.to_thread(path.read_text))

    await Replayer(
        workflows=[ChatWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)

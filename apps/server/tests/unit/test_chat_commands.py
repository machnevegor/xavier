"""Delivery of the soft delete command to `ChatWorkflow`."""

import uuid
from typing import cast

import pytest
from temporalio.client import Client, WithStartWorkflowOperation, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from src.db.models import ChatStatus
from src.errors import ChatDeletedError
from src.orchestration.client import ChatCommands
from src.orchestration.models import CommandResult, DeleteChat
from src.orchestration.workflows import CHAT_DELETED


class FakeClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.updates: list[tuple[str, object]] = []

    async def execute_update_with_start_workflow(
        self,
        update: str,
        arg: object,
        *,
        start_workflow_operation: WithStartWorkflowOperation,
        result_type: type[CommandResult],
    ) -> CommandResult:
        del start_workflow_operation, result_type
        self.updates.append((update, arg))
        if self.error is not None:
            raise self.error
        return CommandResult(status=ChatStatus.READY)


async def test_delete_sends_the_delete_update_with_the_deleting_user() -> None:
    client = FakeClient()
    user_id = uuid.uuid4()

    result = await ChatCommands(cast(Client, client), "xavier").delete(uuid.uuid4(), user_id)

    assert client.updates == [("delete", DeleteChat(deleted_by=user_id))]
    assert result.status is ChatStatus.READY


async def test_a_deleted_chat_refuses_the_command_with_a_conflict() -> None:
    error = WorkflowUpdateFailedError(ApplicationError("the chat is deleted", type=CHAT_DELETED))
    client = FakeClient(error)

    with pytest.raises(ChatDeletedError) as raised:
        await ChatCommands(cast(Client, client), "xavier").delete(uuid.uuid4(), uuid.uuid4())

    assert raised.value.status_code == 409

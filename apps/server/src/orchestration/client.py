"""Temporal client and delivery of chat commands to `ChatWorkflow`."""

from collections.abc import Awaitable
from typing import Literal
from uuid import UUID

from temporalio.client import (
    Client,
    WithStartWorkflowOperation,
    WorkflowUpdateFailedError,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from types_aiobotocore_s3.client import S3Client

from src.agent.notices import Notice
from src.config import Settings
from src.errors import ChatDeletedError, UnavailableError
from src.orchestration.converter import create_data_converter
from src.orchestration.models import CommandResult, DeleteChat, EditMessage, Rewound
from src.orchestration.workflows import CHAT_DELETED, ChatWorkflow


async def connect_temporal(settings: Settings, s3: S3Client) -> Client:
    """Connect to the Temporal Service with the Xavier data converter and tracing."""
    converter = create_data_converter(
        s3, settings.s3.temporal_bucket, settings.temporal.payload_size_threshold
    )
    return await Client.connect(
        settings.temporal.address,
        namespace=settings.temporal.namespace,
        data_converter=converter,
        interceptors=[TracingInterceptor()],
    )


class ChatCommands:
    """Deliver operator input and commands to the chat workflows."""

    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def input_available(self, chat_id: UUID) -> None:
        """Signal new input, starting the workflow if it is not running."""
        await self._signal(chat_id, "input_available")

    async def action_decided(self, chat_id: UUID, action_id: UUID) -> None:
        """Signal a decision, starting the workflow if it is not running."""
        await self._signal(chat_id, "action_decided", action_id)

    async def settings_changed(self, chat_id: UUID, *, proactive: bool) -> None:
        """Signal changed chat settings, starting the workflow if it is not running."""
        await self._signal(chat_id, "settings_changed", proactive)

    async def chat_event(self, chat_id: UUID, notice: Notice) -> None:
        """Report a finished job or an alert, starting the workflow if it is not running."""
        await self._signal(chat_id, "chat_event", notice)

    async def edit_message(self, chat_id: UUID, edit: EditMessage) -> Rewound:
        """Revise a message, starting the workflow if it is not running."""
        operation = WithStartWorkflowOperation(
            ChatWorkflow.run,
            chat_id,
            id=str(chat_id),
            task_queue=self._task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return await _deliver(
            self._client.execute_update_with_start_workflow(
                "edit_message", edit, start_workflow_operation=operation, result_type=Rewound
            )
        )

    async def workspace(
        self,
        chat_id: UUID,
        signal: Literal["wake_workspace", "hibernate_workspace", "delete_workspace"],
    ) -> None:
        """Send a workspace request, starting the workflow if it is not running."""
        await self._signal(chat_id, signal)

    async def pause(self, chat_id: UUID) -> CommandResult | None:
        """Pause the chat; ``None`` when no workflow is running."""
        return await self._update(chat_id, "pause")

    async def resume(self, chat_id: UUID) -> CommandResult | None:
        """Resume the chat; ``None`` when no workflow is running."""
        return await self._update(chat_id, "resume")

    async def stop(self, chat_id: UUID) -> CommandResult | None:
        """Stop the current turn; ``None`` when no workflow is running."""
        return await self._update(chat_id, "stop")

    async def delete(self, chat_id: UUID, deleted_by: UUID) -> CommandResult:
        """Delete the chat, starting the workflow if it is not running."""
        operation = WithStartWorkflowOperation(
            ChatWorkflow.run,
            chat_id,
            id=str(chat_id),
            task_queue=self._task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return await _deliver(
            self._client.execute_update_with_start_workflow(
                "delete",
                DeleteChat(deleted_by=deleted_by),
                start_workflow_operation=operation,
                result_type=CommandResult,
            )
        )

    async def _signal(self, chat_id: UUID, name: str, *args: object) -> None:
        """Send a signal, starting the workflow if it is not running."""
        await _deliver(
            self._client.start_workflow(
                ChatWorkflow.run,
                chat_id,
                id=str(chat_id),
                task_queue=self._task_queue,
                start_signal=name,
                start_signal_args=list(args),
            )
        )

    async def _update(self, chat_id: UUID, name: str) -> CommandResult | None:
        handle = self._client.get_workflow_handle_for(ChatWorkflow.run, str(chat_id))
        try:
            return await _deliver(handle.execute_update(name, result_type=CommandResult))
        except UnavailableError as error:
            cause = error.__cause__
            if isinstance(cause, RPCError) and cause.status is RPCStatusCode.NOT_FOUND:
                return None
            raise


async def _deliver[T](call: Awaitable[T]) -> T:
    """Await a Temporal call; map rejections to domain errors and failures to 503."""
    try:
        return await call
    except WorkflowUpdateFailedError as error:
        cause = error.cause
        if isinstance(cause, ApplicationError) and cause.type == CHAT_DELETED:
            raise ChatDeletedError from error
        raise UnavailableError from error
    except RPCError as error:
        raise UnavailableError from error

"""Deep links to objects shown in a chat."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, Result, Schema

# --- DATA MODEL --- #


class LinkKind(StrEnum):
    """Kind of a linked object."""

    MESSAGE = "message"
    ACTION = "action"
    QUESTION = "question"
    ARTIFACT = "artifact"


class Link(Schema):
    """This object represents where a linked object is shown: its chat and position."""

    kind: LinkKind = Field(description="Kind of the linked object")
    id: UUID = Field(description="Unique identifier of the linked object")
    chat_id: UUID = Field(description="Chat that shows the object")
    turn_id: UUID | None = Field(default=None, description="_Optional_. Turn of the object")
    position: int | None = Field(
        default=None,
        description="_Optional_. For a message: how many current messages of the chat precede "
        "it, to load the page that holds it",
    )


# --- QUERY PARAMS --- #


class ResolveLinkInput(Input):
    """This object represents the input used to resolve a link."""

    kind: LinkKind = Field(description="Kind of the linked object")
    id: UUID = Field(description="Unique identifier of the linked object")


# --- RESULTS --- #


class ResolveLinkResult(Result[Link]):
    """This object represents the result when the link is resolved."""

"""Browser sessions of the signed-in user."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import ListResult, PageQuery, Result, Schema, SortDirection

# --- DATA MODEL --- #


class Session(Schema):
    """This object represents a browser session: a signed-in device."""

    id: UUID = Field(description="Unique session identifier")
    user_agent: str = Field(description="`User-Agent` of the browser that signed in")
    ip: str | None = Field(description="_Optional_. Address the session signed in from")
    current: bool = Field(description="_True_, if the request is made with this session")
    created_at: datetime = Field(description="Date and time of session creation")
    last_seen_at: datetime = Field(description="Date and time of the last request, to a minute")
    expires_at: datetime = Field(description="Date and time the session ends at the latest")
    revoked_at: datetime | None = Field(description="_Optional_. Date and time of sign-out")


# --- QUERY PARAMS --- #


class SessionsSort(StrEnum):
    """Fields sessions can be sorted by."""

    CREATED_AT = "created_at"
    LAST_SEEN_AT = "last_seen_at"
    EXPIRES_AT = "expires_at"


class ListSessionsInput(PageQuery):
    """This object represents the input used to list sessions."""

    sort: SessionsSort = Field(
        default=SessionsSort.LAST_SEEN_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListSessionsResult(ListResult[Session]):
    """This object represents the result when sessions are listed."""


class DeleteSessionResult(Result[Session]):
    """This object represents the result when the session is revoked."""

"""Sign-in and the signed-in user."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, Ok, Result, Schema
from src.auth.service import MAX_PASSWORD, MIN_PASSWORD
from src.db.models import Locale

# --- DATA MODEL --- #


class AuthMethod(StrEnum):
    """How a request is authenticated."""

    SESSION = "session"
    API_KEY = "api_key"


class User(Schema):
    """This object represents a user."""

    id: UUID = Field(description="Unique user identifier")
    username: str = Field(description="Name the user signs in with")
    name: str = Field(description="Name shown in the interface")
    locale: Locale | None = Field(
        description="_Optional_. Interface language; empty until the user signs in from a "
        "browser for the first time"
    )
    created_at: datetime = Field(description="Date and time of user creation")


class Me(Schema):
    """This object represents the signed-in user and how the request is authenticated."""

    user: User = Field(description="The signed-in user")
    method: AuthMethod = Field(
        description="`session` for a browser session cookie, `api_key` for an API key"
    )
    session_id: UUID | None = Field(
        description="_Optional_. Unique session identifier, for a browser session"
    )


# --- INPUTS --- #


class UpdateMeInput(Input):
    """This object represents the input used to update the signed-in user."""

    locale: Locale = Field(description="Interface language")


class SignInInput(Input):
    """This object represents the input used to sign in."""

    username: str = Field(min_length=1, max_length=64, description="Name the user signs in with")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD, description="Password")


class UpdatePasswordInput(Input):
    """This object represents the input used to change the signed-in user's password."""

    current_password: str = Field(
        min_length=1, max_length=MAX_PASSWORD, description="Current password"
    )
    new_password: str = Field(
        min_length=MIN_PASSWORD, max_length=MAX_PASSWORD, description="New password"
    )


# --- ARGS --- #


class UpdateMeArgs(Input):
    """This object represents the arguments used to update the signed-in user."""

    data: UpdateMeInput


class SignInArgs(Input):
    """This object represents the arguments used to sign in."""

    data: SignInInput


class UpdatePasswordArgs(Input):
    """This object represents the arguments used to change the signed-in user's password."""

    data: UpdatePasswordInput


# --- RESULTS --- #


class SignInResult(Result[Me]):
    """This object represents the result when the user signs in."""


class SignOutResult(Ok):
    """This object represents the result when the user signs out."""


class ReadMeResult(Result[Me]):
    """This object represents the result when the signed-in user is read."""


class UpdateMeResult(Result[Me]):
    """This object represents the result when the signed-in user is updated."""


class UpdatePasswordResult(Result[Me]):
    """This object represents the result when the signed-in user's password is changed."""

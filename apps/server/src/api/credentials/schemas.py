"""Credentials: write-only secrets with grants, rotation, revocation and an audit trail."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from src.api.actions.schemas import Action
from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.credentials.hosts import host_pattern
from src.credentials.service import env_name
from src.db import models as db
from src.policy.urls import normalize_url

MAX_CREDENTIAL_HOSTS = 50
MAX_SECRET_CHARS = 10_000
HEADER_NAME = r"^[A-Za-z0-9-]{1,100}$"

HostPatterns = Annotated[
    list[str], AfterValidator(lambda hosts: sorted({host_pattern(host) for host in hosts}))
]
EnvName = Annotated[str, AfterValidator(env_name)]
Url = Annotated[str, AfterValidator(lambda url: normalize_url(url)[0])]


# --- DATA MODEL --- #


class CredentialGrants(Schema):
    """This object represents where a credential may be used."""

    agent: bool = Field(
        description="Tools may use it on the hosts: a token in `web_fetch` headers, a login in "
        "login forms of the Computer's browser"
    )
    watch: bool = Field(description="Observe polls may send a token on the hosts")
    env: str | None = Field(
        description="Environment variable of the chat's `code_run` executions that holds the "
        "value; the executed code can read it and send it anywhere"
    )


class BaseCredential(Schema):
    """Fields every credential has; the secret is never returned."""

    id: UUID = Field(description="Unique credential identifier")
    name: str = Field(description="What the credential is for")
    hosts: list[str] = Field(
        description="Host patterns it may be sent to, `example.com` or `*.example.com`"
    )
    grants: CredentialGrants = Field(description="Where the credential may be used")
    chat_id: UUID | None = Field(
        description="Only this chat may use it; *null* lets every chat of the owner use it"
    )
    status: db.CredentialStatus = Field(description="Whether it is active or revoked")
    version: int = Field(description="Grows with every rotation of the secret")
    expires_at: datetime | None = Field(description="Date and time it stops being usable")
    last_used_at: datetime | None = Field(description="Date and time of the last use")
    failed_at: datetime | None = Field(
        description="Date and time a service rejected it; cleared when the secret is replaced"
    )
    created_at: datetime = Field(description="Date and time of credential creation")
    updated_at: datetime = Field(description="Date and time of last credential update")
    updated_by: UUID | None = Field(description="User who last changed the credential")

    @model_validator(mode="before")
    @classmethod
    def _grants(cls, data: object) -> object:
        if not isinstance(data, db.Credential):
            return data
        fields = {name: getattr(data, name) for name in cls.model_fields if name != "grants"}
        grants = CredentialGrants(
            agent=data.grant_agent, watch=data.grant_watch, env=data.grant_env
        )
        return {**fields, "grants": grants}


class TokenCredential(BaseCredential):
    """This object represents a token credential: a value sent in a request header."""

    secret_type: Literal[db.SecretType.TOKEN] = Field(description="Always *token*")
    header: str | None = Field(description="Request header of the token; *Authorization* when null")
    prefix: str = Field(description="Text before the value, such as `Bearer `")
    refresh_url: str | None = Field(
        description="HTTPS token endpoint on one of the hosts that renews the token with its "
        "refresh token"
    )


class LoginCredential(BaseCredential):
    """This object represents a login credential: a username and a password for login forms."""

    secret_type: Literal[db.SecretType.LOGIN] = Field(description="Always *login*")
    username: str = Field(description="User name; not secret")


Credential = Annotated[TokenCredential | LoginCredential, Field(discriminator="secret_type")]


class CredentialEvent(Schema):
    """This object represents an audit record of a credential; it never contains the value.

    Kinds: *created*, *scope_changed*, *rotated*, *revoked*, *deleted*, *used*, *denied*,
    *rejected*.
    """

    id: UUID = Field(description="Unique event identifier")
    credential_id: UUID = Field(description="Unique credential identifier")
    kind: str = Field(description="What happened")
    chat_id: UUID | None = Field(description="Unique chat identifier, when the use had one")
    action_id: UUID | None = Field(description="Unique action identifier, when the use had one")
    detail: str = Field(description="Extra context of the event, never a secret")
    created_at: datetime = Field(description="Date and time of event creation")


# --- QUERY PARAMS --- #


class CredentialsFilter(Input):
    """Which credentials to list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    is_global: bool | None = Field(
        default=None,
        alias="filter[is_global]",
        description="_Optional_. _True_ for credentials of every chat, _False_ for credentials "
        "of one chat",
    )
    status: list[db.CredentialStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )
    secret_type: list[db.SecretType] | None = Field(
        default=None,
        alias="filter[secret_type]",
        description="_Optional_. Filter by one or more `secret_type` values",
    )


class CredentialsSort(StrEnum):
    """Fields credentials can be sorted by."""

    ID = "id"
    NAME = "name"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListCredentialsInput(CredentialsFilter, PageQuery):
    """This object represents the input used to list credentials."""

    sort: CredentialsSort = Field(
        default=CredentialsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


class CredentialEventsSort(StrEnum):
    """Fields credential events can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListCredentialEventsInput(PageQuery):
    """This object represents the input used to list the audit records of a credential."""

    sort: CredentialEventsSort = Field(
        default=CredentialEventsSort.ID, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CredentialGrantsInput(Input):
    """This object represents where a credential may be used."""

    agent: bool = Field(
        default=False,
        description="_Optional_. Tools may use it on the hosts: a token in `web_fetch` headers, "
        "a login in login forms of the Computer's browser",
    )
    watch: bool = Field(
        default=False, description="_Optional_. Observe polls may send a token on the hosts"
    )
    env: EnvName | None = Field(
        default=None,
        description="_Optional_. Environment variable of the chat's `code_run` executions that "
        "holds the value: `[A-Z_][A-Z0-9_]*`, at most 64 characters, not reserved by the "
        "runtime. The executed code can read the value and send it anywhere",
    )


class BaseCreateCredentialInput(Input):
    """Fields of every new credential."""

    name: str = Field(min_length=1, max_length=100, description="What the credential is for")
    hosts: HostPatterns = Field(
        min_length=1,
        max_length=MAX_CREDENTIAL_HOSTS,
        description="Host patterns it may be sent to, `example.com` or `*.example.com`",
    )
    grants: CredentialGrantsInput = Field(
        default_factory=CredentialGrantsInput,
        description="_Optional_. Where the credential may be used; nothing by default",
    )
    chat_id: UUID | None = Field(
        default=None,
        description="_Optional_. Only this chat may use it; omitted lets every chat of the user "
        "use it",
    )
    expires_at: AwareDatetime | None = Field(
        default=None, description="_Optional_. Date and time it stops being usable"
    )


class CreateTokenCredentialInput(BaseCreateCredentialInput):
    """This object represents the input used to create a token credential."""

    secret_type: Literal[db.SecretType.TOKEN] = Field(description="*token*")
    header: str | None = Field(
        default=None,
        pattern=HEADER_NAME,
        description="_Optional_. Request header; *Authorization* when omitted",
    )
    prefix: str = Field(
        default="",
        max_length=100,
        description="_Optional_. Text before the value, such as `Bearer `",
    )
    refresh_url: Url | None = Field(
        default=None,
        description="_Optional_. HTTPS token endpoint on one of the hosts that renews the token",
    )
    value: str = Field(
        min_length=1, max_length=MAX_SECRET_CHARS, description="Token value, write-only"
    )
    refresh_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SECRET_CHARS,
        description="_Optional_. Renews the token at `refresh_url`, write-only",
    )


class CreateLoginCredentialInput(BaseCreateCredentialInput):
    """This object represents the input used to create a login credential."""

    secret_type: Literal[db.SecretType.LOGIN] = Field(description="*login*")
    username: str = Field(min_length=1, max_length=200, description="User name; not secret")
    password: str = Field(
        min_length=1, max_length=MAX_SECRET_CHARS, description="Password, write-only"
    )


CreateCredentialInput = Annotated[
    CreateTokenCredentialInput | CreateLoginCredentialInput, Field(discriminator="secret_type")
]


class BaseUpdateCredentialInput(Input):
    """Fields every credential update may change; omitted fields keep their values."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="_Optional_. What the credential is for",
    )
    hosts: HostPatterns | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CREDENTIAL_HOSTS,
        description="_Optional_. Host patterns, `example.com` or `*.example.com`",
    )
    grants: CredentialGrantsInput | None = Field(
        default=None, description="_Optional_. Replaces every grant"
    )
    chat_id: UUID | None = Field(
        default=None,
        description="_Optional_. Only this chat may use it; *null* lets every chat of the user "
        "use it",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="_Optional_. Date and time it stops being usable; *null* clears it",
    )


class UpdateTokenCredentialInput(BaseUpdateCredentialInput):
    """This object represents the input used to update a token credential."""

    secret_type: Literal[db.SecretType.TOKEN] = Field(description="*token*, as stored")
    header: str | None = Field(
        default=None,
        pattern=HEADER_NAME,
        description="_Optional_. Request header; *null* sends *Authorization*",
    )
    prefix: str | None = Field(
        default=None, max_length=100, description="_Optional_. Text before the value"
    )
    refresh_url: Url | None = Field(
        default=None,
        description="_Optional_. HTTPS token endpoint on one of the hosts; *null* clears it",
    )
    value: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SECRET_CHARS,
        description="_Optional_. New token value; rotates the secret",
    )
    refresh_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SECRET_CHARS,
        description="_Optional_. Refresh token of the new `value`",
    )

    @model_validator(mode="after")
    def _rotation(self) -> Self:
        if self.refresh_token is not None and self.value is None:
            raise ValueError("`refresh_token` is replaced together with `value`")
        return self


class UpdateLoginCredentialInput(BaseUpdateCredentialInput):
    """This object represents the input used to update a login credential."""

    secret_type: Literal[db.SecretType.LOGIN] = Field(description="*login*, as stored")
    username: str | None = Field(
        default=None, min_length=1, max_length=200, description="_Optional_. User name"
    )
    password: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SECRET_CHARS,
        description="_Optional_. New password; rotates the secret",
    )


UpdateCredentialInput = Annotated[
    UpdateTokenCredentialInput | UpdateLoginCredentialInput, Field(discriminator="secret_type")
]


class AnswerTokenCredentialRequestInput(Input):
    """This object represents the input used to answer a request for a token."""

    secret_type: Literal[db.SecretType.TOKEN] = Field(description="*token*, as requested")
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="_Optional_. Replaces the name the agent proposed",
    )
    header: str | None = Field(
        default=None,
        pattern=HEADER_NAME,
        description="_Optional_. Request header; *Authorization* when omitted",
    )
    prefix: str = Field(
        default="",
        max_length=100,
        description="_Optional_. Text before the value, such as `Bearer `",
    )
    refresh_url: Url | None = Field(
        default=None,
        description="_Optional_. HTTPS token endpoint on one of the hosts that renews the token",
    )
    value: str = Field(
        min_length=1, max_length=MAX_SECRET_CHARS, description="Token value, write-only"
    )
    refresh_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SECRET_CHARS,
        description="_Optional_. Renews the token at `refresh_url`, write-only",
    )


class AnswerLoginCredentialRequestInput(Input):
    """This object represents the input used to answer a request for a login."""

    secret_type: Literal[db.SecretType.LOGIN] = Field(description="*login*, as requested")
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="_Optional_. Replaces the name the agent proposed",
    )
    username: str = Field(min_length=1, max_length=200, description="User name; not secret")
    password: str = Field(
        min_length=1, max_length=MAX_SECRET_CHARS, description="Password, write-only"
    )


AnswerCredentialRequestInput = Annotated[
    AnswerTokenCredentialRequestInput | AnswerLoginCredentialRequestInput,
    Field(discriminator="secret_type"),
]


# --- ARGS --- #


class CreateCredentialArgs(Input):
    """This object represents the arguments used to create a credential."""

    data: CreateCredentialInput


class UpdateCredentialArgs(Input):
    """This object represents the arguments used to update a credential."""

    data: UpdateCredentialInput


class AnswerCredentialRequestArgs(Input):
    """This object represents the arguments used to answer a credential request."""

    data: AnswerCredentialRequestInput


# --- RESULTS --- #


class CreateCredentialResult(Result[Credential]):
    """This object represents the result when the credential is created."""


class ListCredentialsResult(ListResult[Credential]):
    """This object represents the result when credentials are listed."""


class ReadCredentialResult(Result[Credential]):
    """This object represents the result when the credential is read."""


class UpdateCredentialResult(Result[Credential]):
    """This object represents the result when the credential is updated."""


class RevokeCredentialResult(Result[Credential]):
    """This object represents the result when the credential is revoked."""


class DeleteCredentialResult(Result[Credential]):
    """This object represents the result when the credential is deleted."""


class ListCredentialEventsResult(ListResult[CredentialEvent]):
    """This object represents the result when the audit records of a credential are listed."""


class AnswerCredentialRequestResult(Result[Action]):
    """This object represents the result when the credential request is answered and approved."""

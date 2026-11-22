"""Requests for a credential the operator enters."""

from pydantic import Field, JsonValue, field_validator

from src.credentials.service import REQUEST_TOOL, host_pattern
from src.db.models import Credential, SecretType, ToolGroup
from src.policy.model import Risk
from src.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult

MAX_HOSTS = 20
HEADER_PATTERN = r"^[A-Za-z0-9-]{1,100}$"


class CredentialRequestArgs(ToolArgs):
    """Arguments of `request_credential`."""

    name: str = Field(min_length=1, max_length=100, description="What the credential is for.")
    type: SecretType = Field(
        description=(
            "`login` for a username and password typed into a login form of the Computer's "
            "browser; `token` for an API key, bearer token or cookie sent in `web_fetch` "
            "headers."
        )
    )
    hosts: list[str] = Field(
        min_length=1,
        max_length=MAX_HOSTS,
        description="Hosts it may be sent to: `example.com` or `*.example.com`.",
    )
    reason: str = Field(
        min_length=1, max_length=500, description="Why the task needs it; the user reads this."
    )
    username: str | None = Field(
        default=None,
        max_length=200,
        description="_Optional_. For `login`: suggested username the operator can accept.",
    )
    header: str | None = Field(
        default=None,
        pattern=HEADER_PATTERN,
        description="_Optional_. For `token`: HTTP header name, usually `Authorization`.",
    )
    prefix: str | None = Field(
        default=None,
        max_length=100,
        description="_Optional_. For `token`: text before the value, such as `Bearer `.",
    )

    @field_validator("hosts")
    @classmethod
    def _host_patterns(cls, hosts: list[str]) -> list[str]:
        return sorted({host_pattern(host) for host in hosts})


class RequestCredential(Tool[CredentialRequestArgs]):
    """Ask the operator to enter a credential the agent may then use by id."""

    name = REQUEST_TOOL
    groups = frozenset({ToolGroup.ALWAYS})
    description = (
        "Ask the user to enter a login, API key, bearer token or cookie that the task needs. "
        "You get a `credential_id`, never the value: pass it to `web_fetch` or to a `browser` "
        "`type` step, which send it only to the hosts you named. Never ask the user to paste a "
        "secret into the chat, and never put one into code or terminal commands."
    )
    consequences = "Stores the entered secret for this chat, bound to the named hosts."
    risk = Risk.SENSITIVE
    always_ask = True
    args_type = CredentialRequestArgs

    async def run(self, args: CredentialRequestArgs, ctx: ToolContext) -> ToolResult:
        """Return the credential the operator entered for this request."""
        del args
        async with ctx.services.sessions() as session:
            credential = await session.get(Credential, ctx.action_id)
        if credential is None or (credential.created_by, credential.chat_id) != (
            ctx.owner,
            ctx.chat_id,
        ):
            raise ToolError("failed", "the user approved without entering the credential")
        result: dict[str, JsonValue] = {
            "credential_id": str(credential.id),
            "name": credential.name,
            "type": credential.secret_type.value,
            "hosts": list(credential.hosts),
        }
        if credential.username is not None:
            result["username"] = credential.username
        return ToolResult(result)

"""Observe tools."""

import json
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from src.credentials.service import CredentialDeniedError
from src.db.models import SourceStatus, ToolGroup, Version
from src.errors import InvalidRequestError, NotFoundError
from src.observe.extract import ExtractError, ExtractRule, extract
from src.observe.fetch import AUTH_STATUSES, FetchError, FetchResult, to_text
from src.observe.spec import ROOT_KEY, SourceSpec, hosts, widening
from src.policy.model import Resource, ResourceKind, Risk
from src.policy.urls import normalize_url
from src.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult

FETCH_MAX_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 30.0
FETCH_MAX_CHARS = 100_000
RENDER_WAIT_S = 2.0
HISTORY_VALUE_CHARS = 20_000
HISTORY_VERSIONS = 50


class WebFetchArgs(ToolArgs):
    """Arguments of `web_fetch`."""

    url: str = Field(description="http or https URL to fetch.")
    mode: Literal["http", "browser"] = Field(
        default="http",
        description="`http`: a plain HTTP GET. `browser`: render the page in a throwaway "
        "browser first, for pages built by JavaScript.",
    )
    extract: ExtractRule | None = Field(
        default=None, description="Return only the matched values instead of the whole page."
    )
    credential_id: UUID | None = Field(
        default=None,
        description=(
            "`http` mode: authenticate with a credential from the `capabilities` notice. Its "
            "headers go only to the URL's host, never to a redirect elsewhere; you never see "
            "the value."
        ),
    )

    @model_validator(mode="after")
    def _credential_over_http(self) -> Self:
        if self.credential_id is not None and self.mode != "http":
            raise ValueError("`credential_id` works only in `http` mode")
        return self


class WebFetch(Tool[WebFetchArgs]):
    """One-off fetch of a page or API response, normalized to text."""

    name = "web_fetch"
    groups = frozenset({ToolGroup.OBSERVE})
    description = (
        "Fetch a public web page or API response once and return it as readable text: "
        "HTML becomes Markdown-like text with links, JSON is indented. With `extract`, return "
        "only the values matched by a JSONPath or CSS selector. Internal and private addresses "
        "are refused. Use `browser` mode for pages built by JavaScript; use the Computer browser "
        "for pages that need login or interaction."
    )
    consequences = "Loads the URL once; no data is submitted."
    risk = Risk.READ
    interrupt_safe = True
    retryable = True
    args_type = WebFetchArgs

    def prepare(self, args: WebFetchArgs) -> WebFetchArgs:
        """Normalize the URL."""
        url, _host, _port = normalize_url(args.url)
        return args.model_copy(update={"url": url})

    def resources(self, args: WebFetchArgs) -> list[Resource]:
        """Return the URL and its host."""
        _url, host, _port = normalize_url(args.url)
        return [Resource(ResourceKind.URL, args.url), Resource(ResourceKind.HOST, host)]

    async def run(self, args: WebFetchArgs, ctx: ToolContext) -> ToolResult:
        """Fetch and normalize."""
        try:
            result = await self._fetch(args, ctx)
            if args.extract is not None:
                values = extract(result, args.extract)
                text, title = json.dumps(values, indent=1, ensure_ascii=False), None
            else:
                text, title = to_text(result)
        except FetchError as error:
            raise ToolError(error.code, str(error)) from error
        except ExtractError as error:
            raise ToolError("failed", str(error)) from error
        return ToolResult(
            {
                "url": result.url,
                "http_status": result.status,
                "content_type": result.content_type,
                "title": title,
                "text": text[:FETCH_MAX_CHARS],
                "truncated": result.truncated or len(text) > FETCH_MAX_CHARS,
            }
        )

    @staticmethod
    async def _fetch(args: WebFetchArgs, ctx: ToolContext) -> FetchResult:
        if args.mode == "browser":
            return await ctx.services.renderer.render(
                args.url,
                headers={},
                credential={},
                max_bytes=FETCH_MAX_BYTES,
                within=FETCH_TIMEOUT,
                wait_s=RENDER_WAIT_S,
            )
        credential: dict[str, str] = {}
        if args.credential_id is not None:
            try:
                credential = await ctx.services.credentials.headers(
                    args.credential_id,
                    chat_id=ctx.chat_id,
                    owner=ctx.owner,
                    action_id=ctx.action_id,
                    host=normalize_url(args.url)[1],
                )
            except CredentialDeniedError as error:
                raise ToolError("denied", str(error)) from error
        result = await ctx.services.fetcher.fetch(
            args.url, credential=credential, max_bytes=FETCH_MAX_BYTES, within=FETCH_TIMEOUT
        )
        if args.credential_id is not None and result.status in AUTH_STATUSES:
            await ctx.services.credentials.rejected(
                args.credential_id, chat_id=ctx.chat_id, detail=f"http {result.status}"
            )
        return result


class WatchDraftArgs(ToolArgs):
    """Arguments of `watch_draft`."""

    name: str = Field(min_length=1, max_length=100, description="Short name of the source.")
    spec: SourceSpec
    source_id: UUID | None = Field(
        default=None,
        description="Revise this source instead of creating one. A published source keeps "
        "running its current rules until the operator publishes the revision.",
    )


class WatchDraft(Tool[WatchDraftArgs]):
    """Draft of an Observe source that the operator publishes."""

    name = "watch_draft"
    groups = frozenset({ToolGroup.OBSERVE})
    description = (
        "Draft deterministic monitoring of an API or web page: the URL, what to extract, what to "
        "ignore when comparing, child resources to follow and the schedule. Nothing is polled "
        "until the operator reviews and publishes the draft. Every change of the observed value "
        "becomes a version with a diff and an alert. Test extraction with `web_fetch` first."
    )
    consequences = (
        "Stores a draft source, or a revision of an existing one, for the operator to publish; "
        "nothing is fetched yet."
    )
    risk = Risk.WRITE
    args_type = WatchDraftArgs

    def resources(self, args: WatchDraftArgs) -> list[Resource]:
        """Return the URL and every host the source may request."""
        return [
            Resource(ResourceKind.URL, normalize_url(args.spec.url)[0]),
            *(Resource(ResourceKind.HOST, host) for host in sorted(hosts(args.spec))),
        ]

    async def classify(self, args: WatchDraftArgs, ctx: ToolContext) -> Risk:
        """Rate a revision widening a polled source's hosts, credential or limits ``sensitive``."""
        if args.source_id is None:
            return Risk.WRITE
        try:
            source = await ctx.services.sources.source(args.source_id, ctx.owner)
        except NotFoundError:
            return Risk.WRITE
        if source.status is SourceStatus.DRAFT:
            return Risk.WRITE
        current = SourceSpec.model_validate(source.spec)
        return Risk.SENSITIVE if widening(current, args.spec) else Risk.WRITE

    async def run(self, args: WatchDraftArgs, ctx: ToolContext) -> ToolResult:
        """Store the draft or the revision."""
        sources = ctx.services.sources
        try:
            if args.source_id is None:
                source = await sources.draft(args.name, args.spec, ctx.chat_id, ctx.owner)
            else:
                source = await sources.revise(args.source_id, args.name, args.spec, ctx.owner)
        except NotFoundError as error:
            raise ToolError("not_found", error.detail) from error
        current = None if source.status is SourceStatus.DRAFT else source.spec
        widened = widening(
            None if current is None else SourceSpec.model_validate(current), args.spec
        )
        return ToolResult(
            {
                "source_id": str(source.id),
                "status": source.status.value,
                "awaiting_publication": True,
                "widening": list(widened),
                "note": "The operator must review and publish it before polling starts.",
            }
        )


class WatchHistoryArgs(ToolArgs):
    """Arguments of `watch_history`."""

    source_id: UUID
    op: Literal["latest", "versions", "diff"] = Field(
        description="`latest`: the current value. `versions`: recent versions, newest first. "
        "`diff`: changes between two versions, by default the last two."
    )
    resource_key: str = Field(
        default=ROOT_KEY,
        description='`""` for the source URL, `<rule>:<value>` for a child resource.',
    )
    from_version: UUID | None = None
    to_version: UUID | None = None
    limit: int = Field(default=20, ge=1, le=HISTORY_VERSIONS)


class WatchHistory(Tool[WatchHistoryArgs]):
    """History and changes of an Observe source."""

    name = "watch_history"
    groups = frozenset({ToolGroup.OBSERVE})
    description = (
        "Read what an Observe source recorded: the latest observed value, the list of versions "
        "(one per distinct value, with change counts), or the diff between two versions."
    )
    consequences = "Reads stored history; nothing is fetched."
    risk = Risk.READ
    interrupt_safe = True
    retryable = True
    args_type = WatchHistoryArgs

    async def run(self, args: WatchHistoryArgs, ctx: ToolContext) -> ToolResult:
        """Read the history."""
        sources = ctx.services.sources
        try:
            match args.op:
                case "latest":
                    version, value = await sources.latest(
                        args.source_id, ctx.owner, args.resource_key
                    )
                    text = json.dumps(value, ensure_ascii=False)
                    return ToolResult(
                        {
                            "version": _version(version),
                            "value": text[:HISTORY_VALUE_CHARS],
                            "truncated": len(text) > HISTORY_VALUE_CHARS,
                        }
                    )
                case "versions":
                    rows = await sources.versions(
                        args.source_id, ctx.owner, args.resource_key, None, args.limit
                    )
                    return ToolResult({"versions": [_version(row) for row in rows]})
                case "diff":
                    older, newer = args.from_version, args.to_version
                    if older is None or newer is None:
                        recent = await sources.versions(
                            args.source_id, ctx.owner, args.resource_key, None, 2
                        )
                        if len(recent) < 2:  # noqa: PLR2004
                            raise ToolError("not_found", "the resource has fewer than two versions")
                        older, newer = older or recent[1].id, newer or recent[0].id
                    return ToolResult(await sources.diff(args.source_id, ctx.owner, older, newer))
        except NotFoundError as error:
            raise ToolError("not_found", error.detail) from error
        except InvalidRequestError as error:
            raise ToolError("invalid_args", error.detail) from error


def _version(version: Version) -> dict[str, JsonValue]:
    schema_changes = version.schema_changes
    return {
        "id": str(version.id),
        "resource_key": version.resource_key,
        "url": version.url,
        "gone": version.fingerprint is None,
        "change_count": version.change_count,
        "schema_changes": len(schema_changes) if isinstance(schema_changes, list) else 0,
        "created_at": version.created_at.isoformat(),
    }

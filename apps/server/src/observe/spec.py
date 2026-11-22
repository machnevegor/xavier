"""The specification of an Observe source: what to fetch, observe and follow, and when."""

import re
import zoneinfo
from datetime import timedelta
from typing import Literal, Self
from urllib.parse import quote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.observe.extract import ExtractRule
from src.observe.normalize import Normalization, compile_path
from src.policy.urls import ResourceRejectedError, normalize_url

ROOT_KEY = ""
"""Resource key of the source URL itself."""
MIN_INTERVAL = timedelta(minutes=1)
_NAME = r"^[a-z0-9][a-z0-9_-]{0,39}$"
_SECRET_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_PROBE = "x"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChildRule(_Spec):
    """Child resources discovered in the observed value of their parent."""

    name: str = Field(pattern=_NAME, description="Identifier of the rule in resource keys.")
    select: str = Field(
        description="JSONPath over the parent's observed value that yields one value per child."
    )
    url: str = Field(
        description="URL template of a child; `{value}` is replaced by the URL-encoded value. "
        "The placeholder may appear only in the path and query."
    )
    total: str | None = Field(
        default=None,
        description="JSONPath to the number of children the parent declares; a mismatch "
        "means the enumeration is incomplete.",
    )
    recursive: bool = Field(
        default=False, description="Apply this rule again to each child, e.g. for nested pages."
    )
    extract: ExtractRule | None = None
    normalize: Normalization = Normalization()
    children: list[ChildRule] = Field(default=[], max_length=10)

    @field_validator("select", "total")
    @classmethod
    def _valid_path(cls, value: str | None) -> str | None:
        if value is not None:
            compile_path(value)
        return value

    @field_validator("url")
    @classmethod
    def _valid_template(cls, value: str) -> str:
        if "{value}" not in value:
            raise ValueError("the URL template needs the `{value}` placeholder")
        _host(value)
        return value

    def child_url(self, value: object) -> str:
        """Return the URL of the child for ``value``."""
        return self.url.replace("{value}", quote(str(value), safe=""))

    def key(self, value: object) -> str:
        """Return the resource key of the child for ``value``."""
        return f"{self.name}:{value}"


class Limits(_Spec):
    """Limits of one poll; ``null`` takes the configured maximum, and larger values are capped."""

    max_bytes: int | None = Field(default=None, ge=1)
    timeout_s: float | None = Field(default=None, gt=0)
    max_depth: int | None = Field(default=None, ge=0)
    max_fanout: int | None = Field(default=None, ge=1)


class Schedule(_Spec):
    """When the source is polled. Exactly one of `interval` and `cron` is required."""

    interval: timedelta | None = Field(default=None, description="At least one minute.")
    cron: str | None = Field(default=None, description="Cron expression, e.g. `0 9 * * 1-5`.")
    time_zone: str = Field(default="UTC", description="IANA time zone of `cron`.")

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        return self.check_timetable()

    def check_timetable(self) -> Self:
        """Validate that exactly one of a valid `interval` and `cron` is set."""
        if (self.interval is None) == (self.cron is None):
            raise ValueError("pass exactly one of `interval` and `cron`")
        if self.interval is not None and self.interval < MIN_INTERVAL:
            raise ValueError("the interval must be at least one minute")
        if self.cron is not None and len(self.cron.split()) != 5:  # noqa: PLR2004 - cron fields
            raise ValueError("`cron` needs five fields")
        try:
            zoneinfo.ZoneInfo(self.time_zone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown time zone {self.time_zone!r}") from error
        return self


class SourceSpec(_Spec):
    """What an Observe source fetches, observes and follows, and when."""

    url: str = Field(description="http or https URL of the resource.")
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(
        default={},
        max_length=20,
        description="Extra request headers. Secrets go into a credential, not here.",
    )
    body: str | None = Field(default=None, max_length=100_000, description="Body of a POST.")
    credential_id: UUID | None = Field(
        default=None, description="Credential added to every request of the source."
    )
    mode: Literal["http", "browser"] = Field(
        default="http",
        description="`http`: plain requests. `browser`: render each page in a throwaway browser, "
        "for pages that need JavaScript.",
    )
    render_wait_s: float = Field(
        default=2.0, ge=0, le=30, description="`browser`: time scripts get after the page loads."
    )
    extract: ExtractRule | None = None
    normalize: Normalization = Normalization()
    children: list[ChildRule] = Field(default=[], max_length=10)
    limits: Limits = Limits()
    schedule: Schedule

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        _host(value)
        return value

    @field_validator("headers")
    @classmethod
    def _no_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not re.fullmatch(r"[A-Za-z0-9-]+", name):
                raise ValueError(f"invalid header name {name!r}")
            if name.lower() in _SECRET_HEADERS:
                raise ValueError(f"`{name}` must come from a credential")
        return value

    @model_validator(mode="after")
    def _body_with_post(self) -> Self:
        if self.body is not None and self.method != "POST":
            raise ValueError("`body` needs the POST method")
        if self.mode == "browser" and (self.method != "GET" or self.body is not None):
            raise ValueError("`browser` mode only loads pages with GET")
        return self


def _host(url: str) -> str:
    """Return the host of a URL or URL template; the placeholder may not be in the host."""
    if "{value}" in urlsplit(url).netloc:
        raise ValueError("the `{value}` placeholder may not be in the host")
    try:
        _url, host, _port = normalize_url(url.replace("{value}", _PROBE))
    except ResourceRejectedError as error:
        raise ValueError(str(error)) from error
    return host


def rules(spec: SourceSpec) -> list[ChildRule]:
    """Return every child rule of ``spec``, depth first."""
    found: list[ChildRule] = []
    pending = list(spec.children)
    while pending:
        rule = pending.pop(0)
        found.append(rule)
        pending[:0] = rule.children
    return found


def hosts(spec: SourceSpec) -> frozenset[str]:
    """Return every host the source may request."""
    return frozenset({_host(spec.url), *(_host(rule.url) for rule in rules(spec))})


def widening(current: SourceSpec | None, proposed: SourceSpec) -> list[str]:
    """Describe what ``proposed`` allows beyond ``current``: new hosts, a credential, limits.

    Without a current specification every host and the credential count as new.
    """
    known = hosts(current) if current is not None else frozenset()
    found = [f"host {host}" for host in sorted(hosts(proposed) - known)]
    if proposed.credential_id is not None and (
        current is None or proposed.credential_id != current.credential_id
    ):
        found.append(f"credential {proposed.credential_id}")
    if current is not None:
        for name, value in proposed.limits.model_dump().items():
            before = getattr(current.limits, name)
            if before is not None and (value is None or value > before):
                found.append(f"limit {name}")
    return found

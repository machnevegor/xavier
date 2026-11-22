"""Extraction of the observed value from a response: JSONPath for JSON, CSS for HTML."""

import hashlib
import json
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from selectolax.lexbor import LexborHTMLParser

from src.observe.fetch import FetchResult, decode_text, html_to_text
from src.observe.normalize import compile_path

_HTML = frozenset({"text/html", "application/xhtml+xml"})


class ExtractError(ValueError):
    """The response does not have the form the rule expects."""


class ExtractRule(BaseModel):
    """What to observe in a response. Exactly one of `jsonpath` and `css` is required."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jsonpath: str | None = Field(
        default=None, description="JSONPath over a JSON response, e.g. `$.items[*]`."
    )
    css: str | None = Field(default=None, description="CSS selector over an HTML response.")
    attribute: str | None = Field(
        default=None, description="With `css`: take this attribute instead of the element text."
    )

    @field_validator("jsonpath")
    @classmethod
    def _valid_path(cls, value: str | None) -> str | None:
        if value is not None:
            compile_path(value)
        return value

    @field_validator("css")
    @classmethod
    def _valid_selector(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                LexborHTMLParser("<html></html>").css(value)
            except Exception as error:
                raise ValueError(f"invalid CSS selector {value!r}: {error}") from error
        return value

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.jsonpath is None) == (self.css is None):
            raise ValueError("pass exactly one of `jsonpath` and `css`")
        if self.attribute is not None and self.css is None:
            raise ValueError("`attribute` applies only to `css`")
        return self


def is_json(content_type: str) -> bool:
    """Whether the media type is JSON."""
    return content_type == "application/json" or content_type.endswith("+json")


def parse_json(result: FetchResult) -> JsonValue:
    """Return the decoded JSON body.

    Raises
    ------
    ExtractError
        If the body is not JSON.
    """
    try:
        return json.loads(decode_text(result))
    except json.JSONDecodeError as error:
        raise ExtractError(f"the response is not valid JSON: {error}") from error


def extract(result: FetchResult, rule: ExtractRule | None) -> JsonValue:
    """Return the observed value of ``result``.

    Without a rule: JSON as it is, HTML as readable text with its title, other text as it is, and
    binary content as its size and SHA-256. With a rule: the list of matched values.

    Raises
    ------
    ExtractError
        If the response does not have the form the rule expects.
    """
    if rule is None:
        return _whole(result)
    if rule.jsonpath is not None:
        return [match.value for match in compile_path(rule.jsonpath).find(parse_json(result))]
    if not (result.content_type in _HTML or result.content_type.startswith("text/")):
        raise ExtractError(f"a CSS rule needs an HTML response, got {result.content_type}")
    nodes = LexborHTMLParser(decode_text(result)).css(rule.css or "")
    if rule.attribute is not None:
        return [node.attributes.get(rule.attribute) for node in nodes]
    return [node.text(deep=True, separator=" ", strip=True) for node in nodes]


def _whole(result: FetchResult) -> JsonValue:
    if is_json(result.content_type):
        return parse_json(result)
    if result.content_type in _HTML:
        text, title = html_to_text(decode_text(result))
        return {"title": title, "text": text}
    if result.content_type.startswith("text/") or result.content_type.endswith("xml"):
        return decode_text(result)
    return {"size": len(result.body), "sha256": hashlib.sha256(result.body).hexdigest()}

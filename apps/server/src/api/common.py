"""Conventions every resource follows: results, pagination, sorting, errors and tags."""

import inspect
import logging
import math
import socket
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from http import HTTPStatus
from types import ModuleType
from typing import Any, Literal, Protocol
from uuid import UUID

from botocore.exceptions import ConnectionError as S3ConnectionError
from botocore.exceptions import HTTPClientError
from fastapi import APIRouter, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_flat_params
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import create_model_field
from fastapi_derive_responses import AutoDeriveResponsesAPIRoute
from fastapi_derive_responses import _from_dependencies as derive_from_dependencies
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, Select, func, select, tuple_
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from starlette.exceptions import HTTPException

from src.errors import UnavailableError

OUTAGES = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
    OperationalError,
    InterfaceError,
    S3ConnectionError,
    HTTPClientError,
)
"""Failures to reach PostgreSQL or S3; Temporal calls map their own errors."""
PER_PAGE = 50
MAX_PER_PAGE = 200

logger = logging.getLogger(__name__)


# --- DATA MODEL --- #


class Schema(BaseModel):
    """Base of objects the API returns; reads ORM attributes."""

    model_config = ConfigDict(from_attributes=True, use_attribute_docstrings=True)


class Input(BaseModel):
    """Base of request bodies and query parameters; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class PaginationInfo(BaseModel):
    """This object represents a pagination info."""

    cursor: UUID | None = Field(description="_Optional_. ID of the last item")
    page: int = Field(description="Page number (1-based)")
    per_page: int = Field(description="Number of items per page")
    has_next: bool = Field(description="_True_, if the current page is not the last page")
    total_pages: int = Field(description="Total number of pages")
    total_items: int = Field(description="Total number of items")


class FormattedError(BaseModel):
    """This object represents a formatted error."""

    message: str = Field(description="Formatted error message")
    success: Literal[False] = Field(default=False, description="Always _False_")


class ValidationIssue(BaseModel):
    """This object represents one invalid value of a request."""

    loc: list[str | int] = Field(description="Path of the value: `body`, `query` or `path` first")
    msg: str = Field(description="What is wrong with the value")
    type: str = Field(description="Kind of the problem")


class ValidationError(BaseModel):
    """This object represents a validation error."""

    message: str = Field(description="Formatted error message")
    details: list[ValidationIssue] = Field(description="Validation error details")
    success: Literal[False] = Field(default=False, description="Always _False_")


# --- QUERY PARAMS --- #


class SortDirection(StrEnum):
    """Order of a sorted list."""

    ASC = "asc"
    DESC = "desc"


class OffsetQuery(Input):
    """Position and size of a page of a list without stable item ids."""

    page: int = Field(default=1, ge=1, description="_Optional_. Page number (1-based)")
    per_page: int = Field(
        default=PER_PAGE, ge=1, le=MAX_PER_PAGE, description="_Optional_. Number of items per page"
    )


class PageQuery(OffsetQuery):
    """Position and size of a page."""

    cursor: UUID | None = Field(
        default=None,
        description=(
            "_Optional_. ID of the last item from the previous page. Can be used with `page` "
            "to anchor pagination and avoid shifts when new items are added"
        ),
    )


# --- RESULTS --- #


class Result[T](BaseModel):
    """One object."""

    data: T
    success: Literal[True] = Field(default=True, description="Always _True_")


class ListResult[T](BaseModel):
    """One page of a list."""

    data: list[T]
    info: PaginationInfo
    success: Literal[True] = Field(default=True, description="Always _True_")


class Ok(BaseModel):
    """An operation without a resulting object."""

    success: Literal[True] = Field(default=True, description="Always _True_")


# --- ROUTE RESPONSES --- #


def formatted(status: int) -> dict[str, Any]:
    """Return the OpenAPI response of a `FormattedError` with ``status``."""
    return {"model": FormattedError, "description": HTTPStatus(status).phrase}


UNAUTHORIZED = formatted(401)
FORBIDDEN = formatted(403)
NOT_FOUND = formatted(404)
CONFLICT = formatted(409)
PAYLOAD_TOO_LARGE = formatted(413)
UNAVAILABLE = formatted(503)
INVALID_REQUEST = formatted(422)
UNPROCESSABLE_ENTITY: dict[str, Any] = {
    "model": ValidationError,
    "description": HTTPStatus(422).phrase,
}


def _function_dependencies(dependant: Dependant) -> list[Dependant]:
    found: list[Dependant] = []
    for sub in dependant.dependencies:
        if inspect.isfunction(sub.call) or inspect.ismethod(sub.call):
            found.append(sub)
        found.extend(_function_dependencies(sub))
    return found


class ApiRoute(AutoDeriveResponsesAPIRoute):
    """A route whose error responses follow from the errors it can raise.

    `AutoDeriveResponsesAPIRoute` reads the `HTTPException` subclasses raised by the endpoint
    and its direct dependencies. Errors of nested dependencies, such as the 401 of
    `CurrentUserDep` behind `OwnedChatDep`, are added here, and every derived error is
    documented as a `FormattedError`; request validation as a `ValidationError`. Errors raised
    deeper in services are declared on the route with the maps of this module.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:  # noqa: ANN401 - forwarded to APIRoute
        super().__init__(path, endpoint, **kwargs)
        nested = [sub for top in self.dependant.dependencies for sub in _function_dependencies(top)]
        derived = set(derive_from_dependencies(nested)) | {
            int(code)
            for code, response in self.responses.items()
            if int(code) >= HTTPStatus.BAD_REQUEST
            and response.get("model", FormattedError) is FormattedError
        }
        for code in derived:
            self.responses[code] = formatted(int(code))
        if get_flat_params(self.dependant) or self.body_field is not None:
            self.responses[HTTPStatus.UNPROCESSABLE_ENTITY.value] = (
                {**UNPROCESSABLE_ENTITY, "model": ValidationError | FormattedError}
                if HTTPStatus.UNPROCESSABLE_ENTITY in derived
                else UNPROCESSABLE_ENTITY
            )
        self.response_fields = {
            code: create_model_field(
                name=f"Response_{code}_{self.unique_id}",
                type_=response["model"],
                mode="serialization",
            )
            for code, response in self.responses.items()
            if "model" in response
        }


def router(prefix: str, tag: str) -> APIRouter:
    """Return the router of a resource; its module docstring documents ``tag``."""
    return APIRouter(prefix=prefix, tags=[tag], route_class=ApiRoute)


def populate_openapi_tags(app: FastAPI, router_module: ModuleType) -> None:
    """Document the tag of ``router_module.router`` with the module docstring."""
    resource: APIRouter = router_module.router
    app.openapi_tags = [
        *(app.openapi_tags or []),
        {
            "name": str(resource.tags[0]),
            "description": inspect.cleandoc(router_module.__doc__ or ""),
        },
    ]


# --- PAGINATION --- #


class _Identified(Protocol):
    @property
    def id(self) -> UUID: ...


async def paginate[T: _Identified](  # noqa: PLR0913 - the parts of one list query
    session: AsyncSession,
    query: Select[T],
    params: PageQuery,
    *,
    sort: InstrumentedAttribute[Any],
    direction: SortDirection,
    key: InstrumentedAttribute[UUID],
) -> tuple[list[T], PaginationInfo]:
    """Return one page of ``query`` ordered by ``sort`` then ``key``, with its position.

    A ``cursor`` anchors the list after that item, and ``page`` counts from the anchor, so
    items added before it do not shift the pages.

    Raises
    ------
    RequestValidationError
        If the cursor is not an item of the list.
    """
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    ascending = direction is SortDirection.ASC
    ordered = query.order_by(
        sort.asc() if ascending else sort.desc(), key.asc() if ascending else key.desc()
    )
    if params.cursor is not None:
        # The anchor has to be a row of this filtered list, not any row with that id.
        visible = query.order_by(None).subquery()
        anchor = (
            await session.execute(
                select(sort, key).where(key == params.cursor, key.in_(select(visible.c[key.key])))
            )
        ).first()
        if anchor is None:
            raise RequestValidationError(
                [
                    {
                        "loc": ("query", "cursor"),
                        "msg": "Not an item of the list",
                        "type": "value_error",
                        "input": str(params.cursor),
                    }
                ]
            )
        position = tuple_(sort, key)
        after: ColumnElement[bool] = (
            position > tuple(anchor) if ascending else position < tuple(anchor)
        )
        ordered = ordered.where(after)
    rows = list(
        (
            await session.scalars(
                ordered.offset((params.page - 1) * params.per_page).limit(params.per_page + 1)
            )
        ).all()
    )
    has_next = len(rows) > params.per_page
    rows = rows[: params.per_page]
    return rows, _info(params, total or 0, has_next=has_next, cursor=rows[-1].id if rows else None)


def paginate_items[T](items: Sequence[T], params: OffsetQuery) -> tuple[list[T], PaginationInfo]:
    """Return one page of ``items``, a list held in memory and already ordered."""
    start = (params.page - 1) * params.per_page
    page = list(items[start : start + params.per_page])
    return page, _info(
        params, len(items), has_next=start + params.per_page < len(items), cursor=None
    )


def _info(
    params: OffsetQuery, total: int, *, has_next: bool, cursor: UUID | None
) -> PaginationInfo:
    return PaginationInfo(
        cursor=cursor,
        page=params.page,
        per_page=params.per_page,
        has_next=has_next,
        total_pages=math.ceil(total / params.per_page),
        total_items=total,
    )


# --- ERROR HANDLERS --- #


def _error(status: int, message: str, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        FormattedError(message=message).model_dump(), status_code=status, headers=headers
    )


def install(app: FastAPI) -> None:
    """Render errors as `FormattedError`, and request validation as `ValidationError`."""

    @app.exception_handler(HTTPException)
    async def http(request: Request, error: HTTPException) -> JSONResponse:
        if error.status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
            logger.warning("%s %s: %s", request.method, request.url.path, error.status_code)
        else:
            logger.error(
                "%s %s: %s", request.method, request.url.path, error.status_code, exc_info=error
            )
        return _error(error.status_code, str(error.detail), error.headers)

    async def outage(request: Request, error: Exception) -> JSONResponse:
        logger.warning(
            "%s %s: dependency unreachable: %s: %s",
            request.method,
            request.url.path,
            type(error).__name__,
            error,
        )
        return _error(UnavailableError.status_code, UnavailableError.message)

    for outage_type in OUTAGES:
        app.add_exception_handler(outage_type, outage)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            ValidationIssue(loc=list(issue["loc"]), msg=issue["msg"], type=issue["type"])
            for issue in error.errors()
        ]
        logger.warning(
            "invalid request %s %s: %s",
            request.method,
            request.url.path,
            [(issue.loc, issue.type) for issue in details],
        )
        message = (
            f"{'.'.join(str(part) for part in details[0].loc)}: {details[0].msg}"
            if details
            else HTTPStatus(422).phrase
        )
        body = ValidationError(message=message, details=details)
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.error("%s %s failed", request.method, request.url.path, exc_info=error)
        return _error(500, HTTPStatus(500).phrase)

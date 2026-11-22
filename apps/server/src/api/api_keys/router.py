"""API keys of the user, for programmatic access.

A request authenticates with a key sent as `Authorization: Bearer xv_…`; it acts as the user
who created the key. The full key is returned once, when it is created; only its prefix is
kept readable. Revoked keys are not listed and stop working at once. Keys are managed only from
a signed-in browser; requests with an API key get 403.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query, status
from sqlalchemy import select

from src.api.api_keys.schemas import (
    ApiKey,
    ApiKeysSort,
    CreateApiKeyArgs,
    CreateApiKeyResult,
    DeleteApiKeyResult,
    ListApiKeysInput,
    ListApiKeysResult,
)
from src.api.common import INVALID_REQUEST, NOT_FOUND, paginate, router
from src.api.dependencies import AccountsDep, BrowserUserDep, SessionDep
from src.db import models as db

router = router("/api-keys", "api-keys")

_SORT = {
    ApiKeysSort.ID: db.ApiKey.id,
    ApiKeysSort.CREATED_AT: db.ApiKey.created_at,
    ApiKeysSort.NAME: db.ApiKey.name,
}


@router.get("")
async def list_api_keys(
    params: Annotated[ListApiKeysInput, Query()], principal: BrowserUserDep, session: SessionDep
) -> ListApiKeysResult:
    """List the user's API keys that are not revoked."""
    query = select(db.ApiKey).where(
        db.ApiKey.user_id == principal.user_id, db.ApiKey.revoked_at.is_(None)
    )
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.ApiKey.id,
    )
    return ListApiKeysResult(data=[ApiKey.model_validate(row) for row in rows], info=info)


@router.post("", status_code=status.HTTP_201_CREATED, responses={422: INVALID_REQUEST})
async def create_api_key(
    args: CreateApiKeyArgs, principal: BrowserUserDep, accounts: AccountsDep
) -> CreateApiKeyResult:
    """Create an API key. The full key is in `key` of this response only; store it right away."""
    created = await accounts.create_key(principal.user_id, args.data.name)
    return CreateApiKeyResult(data=ApiKey.model_validate(created.key), key=created.value)


@router.delete("/{api_key_id}", responses={404: NOT_FOUND})
async def delete_api_key(
    api_key_id: UUID, principal: BrowserUserDep, accounts: AccountsDep
) -> DeleteApiKeyResult:
    """Revoke an API key; requests with it fail from now on."""
    revoked = await accounts.revoke_key(principal.user_id, api_key_id)
    return DeleteApiKeyResult(data=ApiKey.model_validate(revoked))

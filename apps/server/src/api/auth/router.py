"""Sign-in with a username and password, sign-out and the signed-in user.

Signing in sets the `xavier_session` cookie: HttpOnly, `SameSite=Strict`, and `Secure` when
the API is served over HTTPS. Browsers authenticate every request with it, event streams and
WebSocket connections included. Programs authenticate with an API key instead, sent as
`Authorization: Bearer xv_…`.
"""

from fastapi import Request, Response, status

from src.api.auth.schemas import (
    AuthMethod,
    Me,
    ReadMeResult,
    SignInArgs,
    SignInResult,
    SignOutResult,
    UpdateMeArgs,
    UpdateMeResult,
    UpdatePasswordArgs,
    UpdatePasswordResult,
    User,
)
from src.api.common import router
from src.api.dependencies import (
    SESSION_COOKIE,
    AccountsDep,
    BrowserUserDep,
    CurrentUserDep,
    ServicesDep,
)
from src.auth.service import Principal
from src.db import models as db
from src.errors import InvalidRequestError, UnauthorizedError

router = router("/auth", "auth")


@router.post("/session", status_code=status.HTTP_201_CREATED)
async def sign_in(
    args: SignInArgs,
    request: Request,
    response: Response,
    accounts: AccountsDep,
    services: ServicesDep,
) -> SignInResult:
    """Sign in and set the session cookie.

    A wrong username or password returns 401 without telling which one was wrong.
    """
    user = await accounts.verify(args.data.username, args.data.password)
    if user is None:
        raise UnauthorizedError("wrong username or password")
    created = await accounts.start_session(
        user.id,
        user_agent=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else None,
    )
    response.set_cookie(
        SESSION_COOKIE,
        created.token,
        max_age=int(services.settings.auth.session_ttl.total_seconds()),
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    return SignInResult(data=_me(user, Principal(user.id, session_id=created.session.id)))


@router.delete("/session")
async def sign_out(request: Request, response: Response, accounts: AccountsDep) -> SignOutResult:
    """Sign out: end the session of the cookie, if it is still valid, and remove the cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    principal = await accounts.resolve(token) if token else None
    if principal is not None and principal.session_id is not None:
        await accounts.revoke_session(principal.user_id, principal.session_id)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    return SignOutResult()


@router.get("/me")
async def read_me(principal: CurrentUserDep, accounts: AccountsDep) -> ReadMeResult:
    """Return the signed-in user and how the request is authenticated."""
    return ReadMeResult(data=_me(await accounts.user(principal.user_id), principal))


@router.patch("/me")
async def update_me(
    args: UpdateMeArgs, principal: CurrentUserDep, accounts: AccountsDep
) -> UpdateMeResult:
    """Set the interface language of the signed-in user.

    The choice applies to later sessions and other devices. A browser session and an API key
    can both change the caller's own profile.
    """
    await accounts.set_locale(principal.user_id, args.data.locale, by=principal.user_id)
    return UpdateMeResult(data=_me(await accounts.user(principal.user_id), principal))


@router.patch("/me/password")
async def update_password(
    args: UpdatePasswordArgs, principal: BrowserUserDep, accounts: AccountsDep
) -> UpdatePasswordResult:
    """Change the signed-in user's password.

    The old password must be valid. Other browser sessions are signed out; the browser
    session making this request remains valid. API keys cannot change the account password.
    """
    user = await accounts.user(principal.user_id)
    if await accounts.verify(user.username, args.data.current_password) is None:
        raise InvalidRequestError("wrong current password")
    await accounts.set_password(
        user.id,
        args.data.new_password,
        by=user.id,
        keep=principal.session_id,
    )
    return UpdatePasswordResult(data=_me(user, principal))


def _me(user: db.User, principal: Principal) -> Me:
    return Me(
        user=User.model_validate(user),
        method=AuthMethod.SESSION if principal.session_id is not None else AuthMethod.API_KEY,
        session_id=principal.session_id,
    )

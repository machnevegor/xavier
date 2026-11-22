"""Domain errors with an HTTP status.

They subclass `HTTPException` so that routes derive their error responses from the errors they
raise; the API renders every one as a `FormattedError` with its message.
"""

from starlette.exceptions import HTTPException


class XavierError(HTTPException):
    """Base of errors that reach API clients."""

    status_code = 500
    message = "Internal error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(self.status_code, detail or self.message)

    def __str__(self) -> str:
        """Return the message, without the status `HTTPException` puts first."""
        return self.detail


class UnauthorizedError(XavierError):
    """The request carries no valid session or API key."""

    status_code = 401
    message = "Authentication required"


class ForbiddenError(XavierError):
    """The caller is known but may not do this, such as managing keys with an API key."""

    status_code = 403
    message = "Forbidden"


class NotFoundError(XavierError):
    """The resource does not exist or belongs to another user."""

    status_code = 404
    message = "Not found"


class InvalidRequestError(XavierError):
    """The request is well-formed but not acceptable."""

    status_code = 422
    message = "Invalid request"


class ConflictError(XavierError):
    """The resource is in a state that does not allow the operation."""

    status_code = 409
    message = "Conflict"


class ChatDeletedError(ConflictError):
    """The chat was deleted while the request was handled; it accepts no input or commands."""

    message = "Chat is deleted"


class AlreadyDecidedError(ConflictError):
    """The action already has a different decision or is no longer waiting for one."""

    message = "Action already decided"


class BudgetExhaustedError(ConflictError):
    """The chat was paused by its budget, which must be raised before resuming."""

    message = "Chat budget exhausted"


class WorkspaceHibernatedError(ConflictError):
    """The workspace is hibernated; wake it to access files."""

    message = "Workspace is hibernated"


class ComputerBusyError(ConflictError):
    """An agent call holds the Computer; control can be taken when it ends."""

    message = "Computer is busy"


class PayloadTooLargeError(XavierError):
    """The uploaded content exceeds the limit."""

    status_code = 413
    message = "Payload too large"


class UnavailableError(XavierError):
    """A dependency did not accept the command; the state is consistent and a retry is safe."""

    status_code = 503
    message = "Service unavailable"

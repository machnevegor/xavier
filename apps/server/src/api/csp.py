"""Content Security Policy of every HTTP response."""

import base64
import hashlib
import re

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_POLICY = "default-src 'self'; img-src 'self' data:"
"""``data:`` images: the Swagger UI stylesheet draws its icons through them."""
_INLINE_SCRIPT = re.compile(rb"<script>(.*?)</script>", re.DOTALL)


def docs_policy(html: bytes) -> str:
    """Return the policy of a Swagger UI page: ``'self'`` plus hashes of its inline scripts."""
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(script).digest()).decode()}'"
        for script in _INLINE_SCRIPT.findall(html)
    )
    return f"{DEFAULT_POLICY}; script-src 'self' {hashes}"


class ContentSecurityPolicy:
    """Send `DEFAULT_POLICY` and ``nosniff`` unless the response sets its own policy.

    The Swagger UI page at ``docs_url`` boots from an inline script, so its policy also allows
    the hashes of the inline scripts it contains.
    """

    def __init__(self, app: ASGIApp, docs_url: str) -> None:
        self._app = app
        self._docs_url = docs_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request, adding the policy to its response."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        docs = scope["path"] == self._docs_url
        start: Message = {}
        body = bytearray()

        async def secured(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("content-security-policy", DEFAULT_POLICY)
                headers.setdefault("x-content-type-options", "nosniff")
                if docs:
                    start = message
                    return
            elif docs and message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return
                MutableHeaders(scope=start)["content-security-policy"] = docs_policy(bytes(body))
                await send(start)
                message = {"type": "http.response.body", "body": bytes(body)}
            await send(message)

        await self._app(scope, receive, secured)

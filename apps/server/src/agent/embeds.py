"""Media the agent references in its answers.

``file:///workspace/...`` references become chat artifacts, so the answer keeps showing them after
the workspace hibernates or changes. HTTPS images are listed for the media proxy, which serves
only URLs that occur in the event.
"""

import contextlib
import hashlib
import mimetypes
import posixpath
import re
from urllib.parse import unquote
from uuid import UUID

from pydantic import JsonValue

from src.observe.fetch import FetchError, HttpFetcher
from src.policy.urls import ResourceRejectedError, normalize_url, workspace_path
from src.storage.artifacts import Artifacts
from src.storage.blobs import BlobNotFoundError, BlobStore
from src.workspace.service import WorkspaceError, WorkspaceService

MAX_EMBED_BYTES = 20 * 1024 * 1024
MAX_EMBEDS = 10
"""References resolved per message; later ones are left as text."""
MAX_MEDIA = 20
"""HTTPS images listed per message."""
MAX_MEDIA_BYTES = 10 * 1024 * 1024
MEDIA_TIMEOUT = 15.0

_RASTER_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_WORKSPACE_REF = re.compile(r"file:///workspace/[^\s)\]\"'<>`]+")
_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?(https://[^\s)>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_TRAILING = ".,;:!?"


class MediaError(Exception):
    """The image cannot be served."""


def workspace_refs(text: str) -> list[str]:
    """Return the distinct ``file:///workspace`` URLs of ``text`` in order of appearance."""
    refs = [match.rstrip(_TRAILING) for match in _WORKSPACE_REF.findall(text)]
    return list(dict.fromkeys(refs))


def image_urls(text: str) -> list[str]:
    """Return the distinct HTTPS URLs of Markdown images in ``text``, at most `MAX_MEDIA`."""
    urls: list[str] = []
    for url in _IMAGE.findall(text):
        try:
            normalize_url(url)
        except ResourceRejectedError:
            continue
        if url not in urls:
            urls.append(url)
    return urls[:MAX_MEDIA]


async def resolve(
    text: str, chat_id: UUID, workspaces: WorkspaceService, artifacts: Artifacts
) -> dict[str, JsonValue]:
    """Return the embed of each workspace reference: an artifact or why there is none.

    Statuses: ``ok`` with ``artifact_id``, ``missing``, ``too_large`` and ``unavailable`` when
    the workspace is not active on this worker.
    """
    refs = workspace_refs(text)[:MAX_EMBEDS]
    if not refs:
        return {}
    active = await workspaces.active_here(chat_id)
    embeds: dict[str, JsonValue] = {}
    for ref in refs:
        try:
            path = workspace_path(unquote(ref.removeprefix("file://")))
        except ResourceRejectedError:
            embeds[ref] = {"status": "missing"}
            continue
        if active is None:
            embeds[ref] = {"status": "unavailable"}
            continue
        try:
            _head, size = await workspaces.read_file(active, path, 0)
        except WorkspaceError:
            embeds[ref] = {"status": "missing"}
            continue
        if size > MAX_EMBED_BYTES:
            embeds[ref] = {"status": "too_large", "size_bytes": size}
            continue
        data, _size = await workspaces.read_file(active, path, MAX_EMBED_BYTES)
        name = posixpath.basename(path)
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        artifact = await artifacts.create_once(
            chat_id=chat_id, name=name, content_type=content_type, data=data, source="embed"
        )
        embeds[ref] = {
            "status": "ok",
            "artifact_id": str(artifact.id),
            "name": name,
            "content_type": content_type,
            "size_bytes": artifact.size_bytes,
        }
    return embeds


def raster_type(data: bytes) -> str | None:
    """Return the type of a PNG, JPEG, GIF or WebP image by its magic bytes; SVG is not raster."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in _RASTER_MAGIC if data.startswith(magic)), None)


async def media(url: str, blobs: BlobStore, fetcher: HttpFetcher) -> tuple[bytes, str]:
    """Return an external image and its type, fetched once and then served from S3.

    The fetch is pinned to a checked public address on every redirect.

    Raises
    ------
    MediaError
        If the image is unreachable, too large or not a raster image.
    """
    key = f"media/{hashlib.sha256(url.encode()).hexdigest()}"
    with contextlib.suppress(BlobNotFoundError):
        data = await blobs.get(key)
        if (kind := raster_type(data)) is not None:
            return data, kind
    try:
        result = await fetcher.fetch(url, max_bytes=MAX_MEDIA_BYTES, within=MEDIA_TIMEOUT)
    except FetchError as error:
        raise MediaError(str(error)) from error
    if result.status != 200:  # noqa: PLR2004
        raise MediaError(f"the image server answered {result.status}")
    if result.truncated:
        raise MediaError(f"the image is larger than {MAX_MEDIA_BYTES} bytes")
    kind = raster_type(result.body)
    if kind is None:
        raise MediaError("not a PNG, JPEG, GIF or WebP image")
    await blobs.put(key, result.body, content_type=kind)
    return result.body, kind

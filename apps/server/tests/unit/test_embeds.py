"""Workspace and HTTPS image references in assistant text."""

from src.agent import embeds


def test_workspace_refs_are_deduplicated_in_order() -> None:
    text = (
        "See file:///workspace/a.png and file:///workspace/b.txt, "
        "then file:///workspace/a.png again."
    )

    assert embeds.workspace_refs(text) == [
        "file:///workspace/a.png",
        "file:///workspace/b.txt",
    ]


def test_image_urls_keep_https_markdown_images_only() -> None:
    text = (
        "![ok](https://cdn.example/a.png) "
        "![private](http://intranet/a.png) "
        "![again](https://cdn.example/a.png) "
        "![ftp](ftp://cdn.example/a.png)"
    )

    assert embeds.image_urls(text) == ["https://cdn.example/a.png"]

from src.api.chats.service import title


def test_title_uses_the_first_useful_line() -> None:
    assert title("Hello world\nMore detail") == "Hello world"
    assert title("") == "Attachment"
    assert title("   \n") == "Attachment"


def test_title_strips_light_markdown() -> None:
    assert title("# Investigate pricing") == "Investigate pricing"
    assert title("- Look at the logs") == "Look at the logs"
    assert title("[OpenRouter docs](https://example.com)") == "OpenRouter docs"


def test_title_truncates_long_lines() -> None:
    long = "x" * 120
    assert title(long).endswith("…")
    assert len(title(long)) == 80

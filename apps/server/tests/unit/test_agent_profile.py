import json
import os
from pathlib import Path

import pytest
import yaml

from src.agent.profile import (
    MCP_SERVER_NAME,
    api_key_env,
    harness_config,
    profile_patch,
    scrub_environment,
)
from src.config import AgentSettings

AGENT = AgentSettings.model_validate(
    {
        "cwd": "/srv/agent",
        "providers": {"open-router": {"base_url": "https://llm.example/v1", "api_key": "llm-key"}},
        "models": [
            {
                "id": "v",
                "provider": "open-router",
                "provider_model": "vendor/v",
                "input": ["text", "image"],
                "thinking_format": "deepseek",
                "reasoning_levels": {"off": None, "high": "high"},
                "pricing_per_mtok": {"input": 1, "output": 2},
            }
        ],
        "default_model": "v",
    }
)
MODEL = AGENT.models[0]


def test_patch_disables_shells_and_file_tools_and_adds_only_the_gateway() -> None:
    patch = json.loads(json.dumps(profile_patch(AGENT, MODEL, "http://127.0.0.1:1/mcp/secret")))

    disabled = {entry["id"] for entry in patch if isinstance(entry, dict) and entry.get("disabled")}
    assert {"persistent-bash", "terminal-bash", "tool-fs"} <= disabled
    [inserted] = [
        entry["insert"] for entry in patch if isinstance(entry, dict) and "insert" in entry
    ]
    assert isinstance(inserted, list)
    assert [entry["id"] for entry in inserted] == [
        "llm-pi-ai",
        "xavier-mcp",
        "user-questions",
        "tool-ask-user",
        "session-title-first-prompt-llm",
    ]
    llm, mcp, questions, ask_user, titles = inserted
    assert isinstance(llm, dict)
    assert isinstance(mcp, dict)
    assert questions == {"id": "user-questions", "name": "@deepseek-ai/dsh-user-questions"}
    assert ask_user == {
        "id": "tool-ask-user",
        "name": "@deepseek-ai/dsh-tool-ask-user",
        "config": {"mode": "timed", "timeout": 120},
    }
    assert titles["id"] == "session-title-first-prompt-llm"
    assert mcp["config"] == {
        "serverName": MCP_SERVER_NAME,
        "transport": "streamable-http",
        "url": "http://127.0.0.1:1/mcp/secret",
        "headers": {},
        "toolCallTimeoutMs": 300_000,
        "startupTimeoutMs": 30_000,
        "closeTimeoutMs": 5_000,
        "expandTemplates": False,
        "failOnStartupError": True,
    }
    provider = llm["config"]["providers"]["open-router"]
    assert provider["apiKeyEnv"] == "DSH_PROVIDER_KEY_OPEN_ROUTER"
    assert provider["models"] == [
        {
            "id": "vendor/v",
            "input": ["text", "image"],
            "reasoningEfforts": {"off": None, "high": "high"},
            "compat": {"thinkingFormat": "deepseek"},
        }
    ]


def test_patch_file_holds_no_secret_and_the_key_travels_in_the_runtime_env(tmp_path: Path) -> None:
    config = harness_config(AGENT, MODEL, reasoning="high", dsh_home=tmp_path, mcp_url="http://x")

    [patch] = config.patches
    text = Path(patch).read_text()
    assert "llm-key" not in text
    assert yaml.safe_load(text) == profile_patch(AGENT, MODEL, "http://x")
    assert Path(patch).stat().st_mode & 0o777 == 0o600
    assert config.cwd == "/srv/agent"
    assert set(config.env) == {"DSH_SYSTEM_PROMPT", api_key_env("open-router")}
    assert config.env[api_key_env("open-router")] == "llm-key"


def test_scrub_environment_removes_only_xavier_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAVIER_DATABASE__PASSWORD", "secret")
    monkeypatch.setenv("XAVIER_LOGGING__LEVEL", "INFO")
    monkeypatch.setenv("UNRELATED_VARIABLE", "kept")

    scrub_environment()

    assert not [name for name in os.environ if name.startswith("XAVIER_")]
    assert os.environ["UNRELATED_VARIABLE"] == "kept"

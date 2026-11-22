"""Harness profile patch and runtime configuration."""

import os
from pathlib import Path

import yaml
from deepseek_harness import DeepSeekHarnessConfig, UserQuestionHandler
from pydantic import JsonValue

from src.agent.prompts import SYSTEM_PROMPT
from src.config import AgentSettings, ModelSettings

PROFILE = "sdk-minimal"
MCP_SERVER_NAME = "xavier"
TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
MCP_TOOL_CALL_TIMEOUT_MS = 300_000
SHUTDOWN_TIMEOUT = 5.0
"""Time the runtime gets to persist an interrupted response before it is killed."""
_DISABLED_PLUGINS = (
    "persistent-bash",
    "persistent-pwsh",
    "terminal-bash",
    "terminal-pwsh",
    "tool-fs",
    "tool-working-directory",
    "mcp-resources",
)
_ENV_PREFIX = "XAVIER_"


def api_key_env(provider: str) -> str:
    """Return the runtime environment variable that carries the provider key."""
    return f"DSH_PROVIDER_KEY_{provider.upper().replace('-', '_')}"


def profile_patch(
    agent: AgentSettings, model: ModelSettings, mcp_url: str, *, question_timeout: int = 120
) -> list[JsonValue]:
    """Return the ``sdk-minimal`` patch: no shells or file tools, one provider and the gateway.

    It adds the native ``ask_user_question`` tool, answered by the host for ``question_timeout``
    seconds unless the model sets another wait, and model-generated session titles.
    """
    provider = agent.providers[model.provider]
    model_entry: dict[str, JsonValue] = {
        "id": model.provider_model,
        "input": list(model.input),
        "reasoningEfforts": dict(model.reasoning_levels),
    }
    if model.thinking_format is not None:
        model_entry["compat"] = {"thinkingFormat": model.thinking_format}
    disabled: list[JsonValue] = [{"id": plugin, "disabled": True} for plugin in _DISABLED_PLUGINS]
    return [
        *disabled,
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "sandbox-policy", "config": {"mode": "read-only"}},
        {
            "insert": [
                {
                    "id": "llm-pi-ai",
                    "name": "@deepseek-ai/dsh-llm-pi-ai",
                    "config": {
                        "providers": {
                            model.provider: {
                                "apiKeyEnv": api_key_env(model.provider),
                                "api": provider.api,
                                "baseURL": provider.base_url,
                                "models": [model_entry],
                            }
                        }
                    },
                },
                {
                    "id": "xavier-mcp",
                    "name": "@deepseek-ai/dsh-mcp-client",
                    "config": {
                        "serverName": MCP_SERVER_NAME,
                        "transport": "streamable-http",
                        "url": mcp_url,
                        "headers": {},
                        "toolCallTimeoutMs": MCP_TOOL_CALL_TIMEOUT_MS,
                        "startupTimeoutMs": 30_000,
                        "closeTimeoutMs": 5_000,
                        "expandTemplates": False,
                        "failOnStartupError": True,
                    },
                },
                {"id": "user-questions", "name": "@deepseek-ai/dsh-user-questions"},
                {
                    "id": "tool-ask-user",
                    "name": "@deepseek-ai/dsh-tool-ask-user",
                    "config": {"mode": "timed", "timeout": question_timeout},
                },
                {
                    "id": "session-title-first-prompt-llm",
                    "name": "@deepseek-ai/dsh-session-title-first-prompt-llm",
                    "config": {
                        "targetWords": 5,
                        "targetCjkCharacters": 10,
                        "maxInputBytes": 4000,
                        "maxOutputTokens": 64,
                        "timeoutMs": 20_000,
                    },
                },
            ]
        },
    ]


def harness_config(  # noqa: PLR0913 - runtime options
    agent: AgentSettings,
    model: ModelSettings,
    *,
    reasoning: str,
    dsh_home: Path,
    mcp_url: str,
    question_timeout: int = 120,
    on_user_question: UserQuestionHandler | None = None,
) -> DeepSeekHarnessConfig:
    """Write the patch into ``dsh_home`` and return the runtime configuration.

    The runtime environment adds only the system prompt and the provider key to the worker
    environment, which holds no secrets (`scrub_environment`).
    """
    patch = dsh_home / "xavier.patch.yml"
    patch.write_text(
        yaml.safe_dump(
            profile_patch(agent, model, mcp_url, question_timeout=question_timeout),
            sort_keys=False,
        )
    )
    patch.chmod(0o600)
    provider = agent.providers[model.provider]
    return DeepSeekHarnessConfig(
        provider=model.provider,
        model=model.provider_model,
        reasoning_effort=reasoning,
        cwd=str(agent.cwd),
        profile=PROFILE,
        patches=(str(patch),),
        dsh_home=str(dsh_home),
        env={
            "DSH_SYSTEM_PROMPT": SYSTEM_PROMPT,
            api_key_env(model.provider): provider.api_key.get_secret_value(),
        },
        initialize_timeout_seconds=60.0,
        request_timeout_seconds=60.0,
        shutdown_timeout_seconds=SHUTDOWN_TIMEOUT,
        on_user_question=on_user_question,
    )


def scrub_environment() -> None:
    """Remove ``XAVIER_*`` variables from the process environment after settings are loaded.

    The Harness runtime inherits the environment of the worker.
    """
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        del os.environ[name]

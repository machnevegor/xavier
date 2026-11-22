import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.__main__ import settings_schema
from src.config import load_settings

ROOT = Path(__file__).parents[2]

MINIMAL = {
    "worker": {"internal_token": "internal-secret"},
    "database": {"password": "db-secret"},
    "s3": {"access_key_id": "access-id", "secret_access_key": "s3-secret"},
    "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
    "agent": {
        "providers": {"main": {"base_url": "https://llm.example/v1", "api_key": "llm-secret"}},
        "models": [
            {
                "id": "small",
                "provider": "main",
                "provider_model": "vendor/small",
                "pricing_per_mtok": {"input": 0.1, "output": 0.2},
            }
        ],
        "default_model": "small",
    },
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("XAVIER_"):
            monkeypatch.delenv(name)


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_loads_yaml_with_defaults(tmp_path: Path) -> None:
    settings = load_settings(_write(tmp_path, MINIMAL))

    assert settings.database.password.get_secret_value() == "db-secret"
    assert settings.temporal.task_queue == "xavier"
    assert settings.logging.format == "json"


def test_environment_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAVIER_DATABASE__PASSWORD", "from-env")
    monkeypatch.setenv("XAVIER_API__PORT", "9000")

    settings = load_settings(_write(tmp_path, MINIMAL))

    assert settings.database.password.get_secret_value() == "from-env"
    assert settings.database.user == "xavier"
    assert settings.api.port == 9000


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    data = {**MINIMAL, "database": {**MINIMAL["database"], "pasword": "typo"}}

    with pytest.raises(ValidationError, match="pasword"):
        load_settings(_write(tmp_path, data))


def test_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "absent.yaml")


def test_secrets_are_masked_and_collected(tmp_path: Path) -> None:
    settings = load_settings(_write(tmp_path, MINIMAL))

    assert "db-secret" not in repr(settings)
    assert sorted(settings.secret_values()) == [
        "access-id",
        "db-secret",
        "internal-secret",
        "llm-secret",
        "s3-secret",
        "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM=",
    ]


def test_models_must_refer_to_known_providers(tmp_path: Path) -> None:
    agent = {**MINIMAL["agent"], "default_model": "absent"}

    with pytest.raises(ValidationError, match="absent"):
        load_settings(_write(tmp_path, {**MINIMAL, "agent": agent}))


def test_internal_url_advertises_worker_id_for_wildcard_host(tmp_path: Path) -> None:
    worker = {"id": "w1", "internal_host": "0.0.0.0", "internal_token": "t"}  # noqa: S104
    data = {**MINIMAL, "worker": worker}

    assert load_settings(_write(tmp_path, data)).worker.internal_url() == "http://w1:8081"


def test_database_url_carries_credentials(tmp_path: Path) -> None:
    url = load_settings(_write(tmp_path, MINIMAL)).database.url()

    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "db-secret"


def test_committed_schema_is_current() -> None:
    committed = (ROOT / "settings.schema.yaml").read_text()

    assert committed == settings_schema(), "run: uv run xavier config-schema > settings.schema.yaml"


def test_example_settings_are_valid() -> None:
    load_settings(ROOT / "settings.example.yaml")

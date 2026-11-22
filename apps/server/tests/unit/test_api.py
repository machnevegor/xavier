"""API contracts that hold without the stack: errors, the OpenAPI document, the docs page."""

import base64
import hashlib
import logging
import re
import uuid

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.api.app import PREFIX, create_app
from src.config import Settings

SETTINGS = Settings.model_validate(
    {
        "worker": {"internal_token": "internal"},
        "database": {"password": "db"},
        "s3": {"access_key_id": "id", "secret_access_key": "secret"},
        "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
        "agent": {
            "providers": {"main": {"base_url": "https://llm.example/v1", "api_key": "key"}},
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
)

# Without entering the context the lifespan, which connects to the stack, does not run.
_APP = create_app(SETTINGS)

_PUBLIC = (f"{PREFIX}/auth/", f"{PREFIX}/docs", f"{PREFIX}/healthz", f"{PREFIX}/readyz")

_PROTECTED = [
    route
    for route in _APP.routes
    if isinstance(route, APIRoute)
    and route.path.startswith(f"{PREFIX}/")
    and not route.path.startswith(_PUBLIC)
]


def _client() -> TestClient:
    return TestClient(_APP)


def test_healthz_reports_ok() -> None:
    response = _client().get(f"{PREFIX}/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_nothing_is_served_outside_the_api_prefix() -> None:
    client = _client()

    for path in ("/", "/docs", "/redoc", "/openapi.json", "/healthz", f"{PREFIX}/redoc"):
        assert client.get(path).status_code == 404, path


def test_unknown_path_is_not_found() -> None:
    response = _client().get(f"{PREFIX}/absent")

    assert response.status_code == 404
    assert response.json() == {"message": "Not Found", "success": False}


def test_handled_client_error_is_logged_without_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="src.api.common"):
        response = _client().get(f"{PREFIX}/absent")

    assert response.status_code == 404
    record = next(item for item in caplog.records if item.name == "src.api.common")
    assert record.levelno == logging.WARNING
    assert "404" in record.message
    assert record.exc_info is None


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(61, "Connect call failed"),
        OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        EndpointConnectionError(endpoint_url="http://s3"),
    ],
)
def test_unreachable_dependency_is_unavailable(error: Exception) -> None:
    app = create_app(SETTINGS)

    @app.get("/outage")
    async def outage() -> None:
        raise error

    response = TestClient(app).get("/outage")

    assert response.status_code == 503
    assert response.json() == {"message": "Service unavailable", "success": False}


@pytest.mark.parametrize("route", _PROTECTED, ids=[route.path for route in _PROTECTED])
def test_route_rejects_requests_without_credentials(route: APIRoute) -> None:
    url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)

    for method in route.methods or ():
        response = _client().request(method, url)

        assert response.status_code == 401, (method, url)
        assert response.json()["success"] is False


def test_openapi_documents_errors_and_pagination() -> None:
    schema = _client().get(f"{PREFIX}/openapi.json").json()

    names = {item["name"] for item in schema["tags"]}
    assert {"auth", "chats", "credentials", "media", "policy"} <= names
    assert all(item["description"] for item in schema["tags"] if item["name"] != "health")

    chats = schema["paths"][f"{PREFIX}/chats"]["get"]
    assert {"page", "per_page", "cursor", "sort", "direction", "filter[status]"} <= {
        parameter["name"] for parameter in chats["parameters"]
    }
    media = schema["paths"][f"{PREFIX}/chats/{{chat_id}}/media"]["get"]
    assert "cursor" not in {parameter["name"] for parameter in media["parameters"]}
    tools = schema["paths"][f"{PREFIX}/tools"]["get"]
    assert {"sort", "direction", "filter[group]"} <= {
        parameter["name"] for parameter in tools["parameters"]
    }
    models = schema["paths"][f"{PREFIX}/models"]["get"]
    assert {"sort", "direction"} <= {parameter["name"] for parameter in models["parameters"]}
    stream = schema["paths"][f"{PREFIX}/chats/{{chat_id}}/events/stream"]["get"]
    assert {"401", "404"} <= stream["responses"].keys()

    sign_in = schema["paths"][f"{PREFIX}/auth/session"]["post"]
    assert "401" in sign_in["responses"]
    me = schema["paths"][f"{PREFIX}/auth/me"]
    assert {"get", "patch"} <= me.keys()
    password = schema["paths"][f"{PREFIX}/auth/me/password"]["patch"]
    assert {"401", "422"} <= password["responses"].keys()
    user = schema["components"]["schemas"]["User"]["properties"]
    assert user["locale"]["anyOf"][0]["$ref"].endswith("/Locale")
    assert schema["components"]["schemas"]["Locale"]["enum"] == ["en", "ru"]
    delete = schema["paths"][f"{PREFIX}/chats/{{chat_id}}"]["delete"]
    assert {"200", "401", "404", "409", "422", "503"} <= delete["responses"].keys()

    components = schema["components"]["schemas"]
    for name in ("Chat", "PolicyRule", "Source", "Subscription", "Alert"):
        assert "updated_by" in components[name]["properties"]
    credential = components["TokenCredential"]["properties"]
    assert "updated_by" in credential
    assert "secret" not in components["Subscription"]["properties"]

    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "list_chats" in operation_ids


def test_swagger_ui_is_served_locally_under_a_strict_policy() -> None:
    client = _client()

    docs = client.get(f"{PREFIX}/docs")

    assert docs.status_code == 200
    policy = docs.headers["content-security-policy"]
    assert policy.startswith("default-src 'self'; img-src 'self' data:; script-src 'self' ")
    inline = re.findall(r"<script>(.*?)</script>", docs.text, re.DOTALL)
    assert inline
    for script in inline:
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert f"{PREFIX}/openapi.json" in docs.text
    assert "cdn" not in docs.text
    for asset in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
        assert client.get(f"{PREFIX}/docs/{asset}").status_code == 200


def test_responses_carry_the_default_policy() -> None:
    response = _client().get(f"{PREFIX}/healthz")

    assert response.headers["content-security-policy"] == "default-src 'self'; img-src 'self' data:"
    assert response.headers["x-content-type-options"] == "nosniff"

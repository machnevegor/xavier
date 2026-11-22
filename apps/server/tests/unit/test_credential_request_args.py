import pytest
from pydantic import ValidationError

from src.db.models import SecretType
from src.tools.credentials import CredentialRequestArgs


def test_token_request_accepts_optional_presentation_fields() -> None:
    args = CredentialRequestArgs.model_validate(
        {
            "name": "API",
            "type": SecretType.TOKEN,
            "hosts": ["api.example.com"],
            "reason": "Needed for polling",
            "header": "Authorization",
            "prefix": "Bearer ",
        }
    )
    assert args.header == "Authorization"
    assert args.prefix == "Bearer "


def test_header_must_be_a_valid_name() -> None:
    with pytest.raises(ValidationError):
        CredentialRequestArgs.model_validate(
            {
                "name": "API",
                "type": SecretType.TOKEN,
                "hosts": ["api.example.com"],
                "reason": "Needed",
                "header": "Auth Header",
            }
        )
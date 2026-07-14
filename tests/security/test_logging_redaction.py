from __future__ import annotations

from app.security.logging import redact_log_payload


def test_redacts_secret_keys_and_nested_values() -> None:
    value = redact_log_payload(
        {
            "api_key": "secret",
            "body": {"authorization": "Bearer abc", "x": 1},
        }
    )
    assert value == {
        "api_key": "[REDACTED]",
        "body": "[REDACTED]",
    }


def test_redacts_sensitive_keys_in_nested_mappings_and_sequences() -> None:
    value = redact_log_payload(
        {
            "headers": [{"X-Token": "token-value"}],
            "outer": {"clientSecret": "secret-value"},
            "items": ({"password_hint": "also-secret"},),
        }
    )
    assert value == {
        "headers": [{"X-Token": "[REDACTED]"}],
        "outer": {"clientSecret": "[REDACTED]"},
        "items": ({"password_hint": "[REDACTED]"},),
    }


def test_redacts_body_and_content_whole_values_case_insensitively() -> None:
    request_body = {"document": "confidential source text", "nested": [1, 2, 3]}
    value = redact_log_payload(
        {
            "BODY": request_body,
            "nested": [{"Content": {"secret": "also hidden"}}],
            "response_body": "do not log",
        }
    )
    assert value == {
        "BODY": "[REDACTED]",
        "nested": [{"Content": "[REDACTED]"}],
        "response_body": "[REDACTED]",
    }
    assert request_body["document"] not in repr(value)


def test_returns_safe_copy_without_mutating_input() -> None:
    payload = {"items": [{"safe": "value"}], "apiKey": "credential"}
    value = redact_log_payload(payload)
    assert value == {"items": [{"safe": "value"}], "apiKey": "[REDACTED]"}
    assert value is not payload
    assert value["items"] is not payload["items"]
    assert value["items"][0] is not payload["items"][0]
    assert payload["apiKey"] == "credential"

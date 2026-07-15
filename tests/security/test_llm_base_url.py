from __future__ import annotations

import pytest

from app.domain.exceptions import ReviewError
from app.security.url_policy import validate_llm_base_url


def test_accepts_public_https():
    assert validate_llm_base_url("https://api.anthropic.com") == "https://api.anthropic.com"


def test_accepts_internal_http_gateway_with_port():
    # Data is desensitized and the gateway is user-configured, so an internal
    # http host with a custom port is allowed for the LLM base URL.
    assert validate_llm_base_url("http://10.0.0.5:8080") == "http://10.0.0.5:8080"


def test_accepts_localhost_gateway():
    assert validate_llm_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_rejects_non_http_scheme():
    for bad in ("ftp://host", "file:///etc/passwd", "javascript:alert(1)", ""):
        with pytest.raises(ReviewError):
            validate_llm_base_url(bad)


def test_rejects_embedded_credentials_and_fragment():
    with pytest.raises(ReviewError):
        validate_llm_base_url("https://user:pass@host")
    with pytest.raises(ReviewError):
        validate_llm_base_url("https://host/path#frag")


def test_rejects_missing_host():
    with pytest.raises(ReviewError):
        validate_llm_base_url("https:///path")

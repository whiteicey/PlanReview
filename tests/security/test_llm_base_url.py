from __future__ import annotations

import pytest

from app.domain.exceptions import ReviewError
from app.security.url_policy import validate_llm_base_url


def test_accepts_public_https():
    assert validate_llm_base_url("https://api.anthropic.com") == "https://api.anthropic.com"


def test_rejects_internal_http_gateway_by_default():
    with pytest.raises(ReviewError):
        validate_llm_base_url("http://10.0.0.5:8080")


def test_private_mode_explicitly_allows_internal_http_gateway():
    assert validate_llm_base_url(
        "http://127.0.0.1:11434", allow_private_endpoint=True
    ) == "http://127.0.0.1:11434"


def test_default_policy_rejects_private_and_nondefault_public_urls():
    for bad in (
        "https://127.0.0.1",
        "https://10.0.0.5",
        "https://169.254.1.2",
        "http://api.example.com",
        "https://api.example.com:8443",
    ):
        with pytest.raises(ReviewError):
            validate_llm_base_url(bad)


def test_private_mode_still_rejects_credentials_fragments_and_non_http_schemes():
    for bad in ("ftp://127.0.0.1", "http://user:pass@127.0.0.1", "http://127.0.0.1#frag"):
        with pytest.raises(ReviewError):
            validate_llm_base_url(bad, allow_private_endpoint=True)


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

from __future__ import annotations

import pytest

from app.domain.exceptions import ReviewError
from app.security.url_policy import validate_base_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "http://localhost:8000",
        "https://localhost/api",
        "https://localhost.localdomain/api",
        "https://127.0.0.1/api",
        "https://[::1]/api",
        "https://10.0.0.2/v1",
        "https://172.16.0.2/v1",
        "https://192.168.0.2/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://192.0.2.1/v1",
        "https://224.0.0.1/v1",
        "https://[ff02::1]/v1",
        "https://[::ffff:224.0.0.1]/v1",
        "https://[fe80::1]/v1",
        "https://u:p@example.com/v1",
        "https://user@example.com/v1",
        "https://api.example.com:8443/v1",
        "https://2130706433/v1",
    ],
)
def test_rejects_unsafe_base_urls(url: str) -> None:
    with pytest.raises(ReviewError, match="Base URL policy rejected"):
        validate_base_url(url)


def test_accepts_public_https() -> None:
    assert validate_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_accepts_default_https_port() -> None:
    assert validate_base_url("https://api.example.com:443/v1") == "https://api.example.com:443/v1"


def test_allowlist_matches_normalized_host_only() -> None:
    assert validate_base_url(
        "https://API.EXAMPLE.COM/v1", {"api.example.com"}
    ) == "https://API.EXAMPLE.COM/v1"
    with pytest.raises(ReviewError, match="not allowlisted"):
        validate_base_url("https://other.example.com/v1", {"api.example.com"})


def test_rejects_malformed_or_relative_urls() -> None:
    for url in ("", "/v1", "https:///v1", "https://api.example.com/v1#fragment"):
        with pytest.raises(ReviewError, match="Base URL policy rejected"):
            validate_base_url(url)

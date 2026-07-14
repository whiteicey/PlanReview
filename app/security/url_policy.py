"""Fail-closed validation for externally configured HTTPS base URLs.

This module performs no DNS lookups and no network I/O. Code that eventually
performs an HTTP request must disable automatic redirects and validate every
redirect target with this function before following it. It must also resolve
and re-check the destination immediately before connecting, so DNS rebinding
cannot turn an approved hostname into a private address.
"""

from __future__ import annotations

from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from app.domain.exceptions import ReviewError


_POLICY_ERROR = "Base URL policy rejected"
_ALLOWLIST_ERROR = "Base URL host not allowlisted"
_NUMERIC_HOST_PATTERN = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+))*$")


def _reject() -> None:
    raise ReviewError(_POLICY_ERROR)


def _normalized_host(host: str) -> str:
    """Return the canonical DNS form, rejecting ambiguous host syntax."""
    if not host or "%" in host:
        _reject()
    try:
        normalized = host.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError:
        _reject()
    if not normalized or _NUMERIC_HOST_PATTERN.fullmatch(normalized):
        _reject()
    return normalized


def _is_public_ip_literal(host: str) -> bool:
    """Return whether an IP literal is globally routable, if ``host`` is one.

    ``is_global`` alone is insufficient here: Python classifies multicast as
    global, and an IPv4-mapped IPv6 address exposes the relevant classification
    on its mapped IPv4 value. Reject every non-routable classification
    explicitly before applying the final global check.
    """
    try:
        address = ip_address(host)
    except ValueError:
        return True

    mapped = getattr(address, "ipv4_mapped", None)
    classified_address = mapped or address
    if any(
        (
            classified_address.is_multicast,
            classified_address.is_private,
            classified_address.is_reserved,
            classified_address.is_loopback,
            classified_address.is_link_local,
            classified_address.is_unspecified,
        )
    ):
        return False
    return address.is_global and classified_address.is_global


def _normalized_allowlist(allowlist: set[str]) -> set[str]:
    normalized: set[str] = set()
    for candidate in allowlist:
        if not isinstance(candidate, str):
            _reject()
        normalized.add(_normalized_host(candidate))
    return normalized


def validate_base_url(url: str, allowlist: set[str] | None = None) -> str:
    """Validate an external HTTPS base URL without dispatching a request.

    Only HTTPS URLs to public hostnames or globally routable IP literals are
    accepted. User credentials, non-default ports, fragments, local/private/
    reserved addresses, and malformed URL forms fail closed. When supplied,
    ``allowlist`` contains permitted hostnames (case-insensitively).
    """
    if not isinstance(url, str) or not url:
        _reject()

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        _reject()

    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        _reject()

    host = parsed.hostname
    if host is None:
        _reject()
    normalized_host = _normalized_host(host)
    if normalized_host in {"localhost", "localhost.localdomain"}:
        _reject()
    if not _is_public_ip_literal(normalized_host):
        _reject()

    if allowlist is not None and normalized_host not in _normalized_allowlist(allowlist):
        raise ReviewError(_ALLOWLIST_ERROR)
    return url

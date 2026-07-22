"""Launch-host guard for the supported local server entry points."""

from __future__ import annotations

from ipaddress import ip_address


def assert_loopback_host(host: str) -> str:
    """Allow only the two explicit loopback addresses supported by this app."""
    if not isinstance(host, str) or not host:
        raise ValueError("supported launch host must be loopback")
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        address = ip_address(candidate)
    except ValueError:
        raise ValueError("supported launch host must be loopback") from None
    if str(address) not in {"127.0.0.1", "::1"}:
        raise ValueError("supported launch host must be loopback")
    return str(address)

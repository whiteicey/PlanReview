from __future__ import annotations

import pytest

from app.security.loopback import assert_loopback_host


def test_supported_launch_hosts_are_exact_loopback_addresses() -> None:
    assert assert_loopback_host("127.0.0.1") == "127.0.0.1"
    assert assert_loopback_host("::1") == "::1"


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.2", "192.168.1.2", "localhost", "::", "2001:db8::1"])
def test_non_loopback_launch_hosts_are_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        assert_loopback_host(host)

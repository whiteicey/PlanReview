from app.main import app
from app.settings import Settings


def test_default_host_is_loopback_only():
    assert Settings().host == "127.0.0.1"


def test_api_does_not_expose_credentials_or_external_service_routes():
    paths = {route.path for route in app.routes}
    assert "/api/config" in paths
    assert all("key" not in path.casefold() for path in paths)
    assert all("http" not in path.casefold() for path in paths)

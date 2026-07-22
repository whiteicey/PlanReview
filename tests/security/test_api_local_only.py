from app.main import app
from app.settings import Settings


def test_default_host_is_loopback_only():
    assert Settings().host == "127.0.0.1"


def test_api_does_not_expose_credentials_or_external_service_routes():
    def paths_for(routes):
        paths = set()
        for route in routes:
            path = getattr(route, "path", None)
            if path is not None:
                paths.add(path)
            nested = getattr(route, "routes", None)
            if nested:
                paths.update(paths_for(nested))
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                paths.update(paths_for(original_router.routes))
        return paths

    paths = paths_for(app.routes)
    assert "/api/config" in paths
    assert all("key" not in path.casefold() for path in paths)
    assert all("http" not in path.casefold() for path in paths)

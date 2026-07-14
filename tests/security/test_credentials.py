from __future__ import annotations

import keyring
import pytest
from keyring.backends.Windows import WinVaultKeyring

import app.security.credentials as credentials_module
from app.security.credentials import CredentialStore


class FakeVaultBackend:
    def __init__(self):
        self.values = {}
        self.calls = []

    def set_password(self, service, provider, key):
        self.calls.append(("set", service, provider, key))
        self.values[(service, provider)] = key

    def get_password(self, service, provider):
        self.calls.append(("get", service, provider))
        return self.values.get((service, provider))

    def delete_password(self, service, provider):
        self.calls.append(("delete", service, provider))
        self.values.pop((service, provider), None)


class SpoofedWinVaultKeyring:
    __module__ = "keyring.backends.Windows"


class FileBackend:
    __module__ = "keyring.backends.file"


def make_fake_store(monkeypatch):
    backend = FakeVaultBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(credentials_module, "_expected_backend_type", lambda: FakeVaultBackend)
    return backend, CredentialStore()


def test_credentials_use_validated_backend_instance(monkeypatch):
    backend, store = make_fake_store(monkeypatch)
    top_level_calls = []
    monkeypatch.setattr(keyring, "set_password", lambda *args: top_level_calls.append(("set", args)))
    monkeypatch.setattr(keyring, "get_password", lambda *args: top_level_calls.append(("get", args)))
    monkeypatch.setattr(keyring, "delete_password", lambda *args: top_level_calls.append(("delete", args)))

    store.set_key("anthropic", "secret-value")
    assert store.get_key("anthropic") == "secret-value"
    store.delete_key("anthropic")
    assert store.get_key("anthropic") is None
    assert [call[0] for call in backend.calls] == ["set", "get", "delete", "get"]
    assert top_level_calls == []


def test_ordinary_constructor_cannot_select_unsafe_backend(monkeypatch):
    backend = FileBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    with pytest.raises(TypeError):
        CredentialStore("review-assistant", _expected_backend_type=FileBackend)
    with pytest.raises(RuntimeError, match="anthropic"):
        CredentialStore().set_key("anthropic", "secret-value")


@pytest.mark.parametrize("backend_factory", [FileBackend, SpoofedWinVaultKeyring])
def test_rejected_backend_never_receives_key(monkeypatch, backend_factory):
    calls = []
    monkeypatch.setattr(keyring, "get_keyring", backend_factory)
    monkeypatch.setattr(keyring, "set_password", lambda *args: calls.append(args))

    with pytest.raises(RuntimeError, match="anthropic") as error:
        CredentialStore().set_key("anthropic", "secret-value")

    assert "secret-value" not in str(error.value)
    assert calls == []


def test_rejected_backend_never_receives_get_or_delete(monkeypatch):
    get_calls = []
    delete_calls = []
    monkeypatch.setattr(keyring, "get_keyring", lambda: FileBackend())
    monkeypatch.setattr(keyring, "get_password", lambda *args: get_calls.append(args))
    monkeypatch.setattr(keyring, "delete_password", lambda *args: delete_calls.append(args))

    for operation in (
        lambda: CredentialStore().get_key("anthropic"),
        lambda: CredentialStore().delete_key("anthropic"),
    ):
        with pytest.raises(RuntimeError, match="anthropic"):
            operation()

    assert get_calls == []
    assert delete_calls == []


def test_validation_then_top_level_backend_swap_does_not_change_target(monkeypatch):
    validated_backend = FakeVaultBackend()
    unsafe_backend = FileBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: validated_backend)
    monkeypatch.setattr(keyring, "set_password", lambda *args: (_ for _ in ()).throw(AssertionError("top-level used")))
    monkeypatch.setattr(credentials_module, "_expected_backend_type", lambda: FakeVaultBackend)
    store = CredentialStore()

    # A top-level lookup swap cannot redirect the already validated instance.
    original_set = validated_backend.set_password
    def swap_then_set(service, provider, key):
        monkeypatch.setattr(keyring, "get_keyring", lambda: unsafe_backend)
        original_set(service, provider, key)
    validated_backend.set_password = swap_then_set

    store.set_key("anthropic", "secret-value")
    assert validated_backend.values[("review-assistant", "anthropic")] == "secret-value"


def test_provider_names_are_validated_before_keyring_calls(monkeypatch):
    backend, store = make_fake_store(monkeypatch)
    for provider in ("", " ", "anthropic/openai", "anthropic\\openai", "../anthropic"):
        with pytest.raises(ValueError, match="provider"):
            store.set_key(provider, "secret-value")
    assert backend.calls == []


def test_get_and_delete_failures_are_sanitized(monkeypatch):
    backend, store = make_fake_store(monkeypatch)
    secret = "get-secret-value"
    backend.get_password = lambda service, provider: (_ for _ in ()).throw(RuntimeError(secret))
    with pytest.raises(RuntimeError, match="anthropic") as get_error:
        store.get_key("anthropic")
    assert secret not in str(get_error.value)

    backend.delete_password = lambda service, provider: (_ for _ in ()).throw(RuntimeError(secret))
    with pytest.raises(RuntimeError, match="anthropic") as delete_error:
        store.delete_key("anthropic")
    assert secret not in str(delete_error.value)


def test_keyring_failures_never_expose_key(monkeypatch):
    backend, store = make_fake_store(monkeypatch)
    secret = "super-secret-value"
    backend.set_password = lambda service, provider, key: (_ for _ in ()).throw(RuntimeError(key))

    with pytest.raises(RuntimeError, match="anthropic") as error:
        store.set_key("anthropic", secret)
    assert secret not in str(error.value)


def test_delete_missing_key_is_idempotent(monkeypatch):
    backend, store = make_fake_store(monkeypatch)
    backend.delete_password = lambda service, provider: (_ for _ in ()).throw(keyring.errors.PasswordDeleteError("not found"))
    store.delete_key("openai")

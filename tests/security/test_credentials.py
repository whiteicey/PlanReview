from __future__ import annotations

import keyring
import pytest

from app.security.credentials import CredentialStore


class WinVaultKeyring:
    __module__ = "keyring.backends.Windows"


class FileBackend:
    __module__ = "keyring.backends.file"


def test_credentials_use_keyring(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    values = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, user, password: values.__setitem__((service, user), password),
    )
    monkeypatch.setattr(
        "keyring.get_password",
        lambda service, user: values.get((service, user)),
    )
    monkeypatch.setattr(
        "keyring.delete_password",
        lambda service, user: values.pop((service, user), None),
    )

    store = CredentialStore()
    store.set_key("anthropic", "secret-value")

    assert store.get_key("anthropic") == "secret-value"
    store.delete_key("anthropic")
    assert store.get_key("anthropic") is None


def test_rejected_backend_never_receives_key(monkeypatch):
    calls = []
    monkeypatch.setattr(keyring, "get_keyring", lambda: FileBackend())
    monkeypatch.setattr(keyring, "set_password", lambda *args: calls.append(args))

    with pytest.raises(RuntimeError, match="anthropic") as error:
        CredentialStore().set_key("anthropic", "secret-value")

    assert "secret-value" not in str(error.value)
    assert calls == []


def test_provider_names_are_validated_before_keyring_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda *args: calls.append(args),
    )
    store = CredentialStore()

    for provider in ("", " ", "anthropic/openai", "anthropic\\openai", "../anthropic"):
        with pytest.raises(ValueError, match="provider"):
            store.set_key(provider, "secret-value")

    assert calls == []


def test_get_and_delete_failures_are_sanitized(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    secret = "get-secret-value"

    def fail_get(service, provider):
        raise RuntimeError(f"backend rejected {secret}")

    def fail_delete(service, provider):
        raise RuntimeError(f"backend rejected {secret}")

    monkeypatch.setattr(keyring, "get_password", fail_get)
    with pytest.raises(RuntimeError, match="anthropic") as get_error:
        CredentialStore().get_key("anthropic")
    assert secret not in str(get_error.value)

    monkeypatch.setattr(keyring, "delete_password", fail_delete)
    with pytest.raises(RuntimeError, match="anthropic") as delete_error:
        CredentialStore().delete_key("anthropic")
    assert secret not in str(delete_error.value)


def test_keyring_failures_never_expose_key(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    secret = "super-secret-value"

    def fail_set(service, provider, key):
        raise RuntimeError(f"backend rejected {key}")

    monkeypatch.setattr(keyring, "set_password", fail_set)

    with pytest.raises(RuntimeError, match="anthropic") as error:
        CredentialStore().set_key("anthropic", secret)

    assert secret not in str(error.value)


def test_delete_missing_key_is_idempotent(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    def missing(service, provider):
        raise keyring.errors.PasswordDeleteError("not found")

    monkeypatch.setattr(keyring, "delete_password", missing)
    CredentialStore().delete_key("openai")

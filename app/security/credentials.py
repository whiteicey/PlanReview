"""Keyring-only storage for provider credentials.

Credential values are passed directly to the system keyring and are never
cached, serialized, or included in errors. On Windows, keyring resolves to the
Windows Credential Manager backend when configured by the environment.
"""

from __future__ import annotations

import re
import sys

import keyring

if sys.platform == "win32":
    from keyring.backends.Windows import WinVaultKeyring
else:  # pragma: no cover - platform guard for non-Windows imports
    WinVaultKeyring = None


_PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DEFAULT_SERVICE = "review-assistant"


class CredentialStoreError(RuntimeError):
    """A keyring operation failed without exposing credential material."""


class CredentialStore:
    """Store provider credentials exclusively through the system keyring."""

    def __init__(self, service: str = _DEFAULT_SERVICE) -> None:
        self.service = self._validate_service(service)

    @staticmethod
    def _validate_service(service: str) -> str:
        if not isinstance(service, str) or not service or len(service) > 128:
            raise ValueError("invalid credential service")
        if not _PROVIDER_PATTERN.fullmatch(service):
            raise ValueError("invalid credential service")
        return service

    @staticmethod
    def _validate_provider(provider: str) -> str:
        if not isinstance(provider, str) or not _PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError("invalid provider")
        return provider

    @staticmethod
    def _validate_key(provider: str, key: str) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError(f"invalid key for provider {provider!r}")
        return key

    @staticmethod
    def _operation_error(provider: str) -> CredentialStoreError:
        return CredentialStoreError(f"keyring operation failed for provider {provider!r}")

    @staticmethod
    def _ensure_windows_credential_manager(provider: str) -> None:
        """Fail closed unless the active backend is Windows Credential Manager."""
        if sys.platform != "win32":
            raise CredentialStoreError(
                f"Windows Credential Manager required for provider {provider!r}"
            )
        try:
            backend = keyring.get_keyring()
        except Exception:
            raise CredentialStoreError(
                f"Windows Credential Manager unavailable for provider {provider!r}"
            ) from None
        if type(backend) is not WinVaultKeyring:
            raise CredentialStoreError(
                f"Windows Credential Manager required for provider {provider!r}"
            )

    def set_key(self, provider: str, key: str) -> None:
        """Store ``key`` under the validated provider username in keyring."""
        provider = self._validate_provider(provider)
        key = self._validate_key(provider, key)
        self._ensure_windows_credential_manager(provider)
        try:
            keyring.set_password(self.service, provider, key)
        except Exception:
            raise self._operation_error(provider) from None

    def get_key(self, provider: str) -> str | None:
        """Retrieve a provider key, or ``None`` when no key is registered."""
        provider = self._validate_provider(provider)
        self._ensure_windows_credential_manager(provider)
        try:
            value = keyring.get_password(self.service, provider)
        except Exception:
            raise self._operation_error(provider) from None
        if value is not None and not isinstance(value, str):
            raise self._operation_error(provider)
        return value

    def delete_key(self, provider: str) -> None:
        """Delete a provider key; deleting an absent key is a no-op."""
        provider = self._validate_provider(provider)
        self._ensure_windows_credential_manager(provider)
        try:
            keyring.delete_password(self.service, provider)
        except keyring.errors.PasswordDeleteError:
            return
        except Exception:
            raise self._operation_error(provider) from None

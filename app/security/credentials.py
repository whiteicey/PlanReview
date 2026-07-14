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

    def __init__(
        self,
        service: str = _DEFAULT_SERVICE,
        *,
        _expected_backend_type: type | None = None,
    ) -> None:
        self.service = self._validate_service(service)
        self._expected_backend_type = _expected_backend_type or WinVaultKeyring

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

    def _validated_backend(self, provider: str):
        """Return the exact validated backend instance used for the operation."""
        if sys.platform != "win32":
            raise CredentialStoreError(
                f"Windows Credential Manager required for provider {provider!r}"
            )
        try:
            backend = keyring.get_keyring()
            if type(backend) is not self._expected_backend_type:
                raise CredentialStoreError(
                    f"Windows Credential Manager required for provider {provider!r}"
                )
            for method_name in ("set_password", "get_password", "delete_password"):
                if not callable(getattr(backend, method_name)):
                    raise CredentialStoreError(
                        f"Windows Credential Manager unavailable for provider {provider!r}"
                    )
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError(
                f"Windows Credential Manager unavailable for provider {provider!r}"
            ) from None
        return backend

    def set_key(self, provider: str, key: str) -> None:
        """Store ``key`` under the validated provider username in keyring."""
        provider = self._validate_provider(provider)
        key = self._validate_key(provider, key)
        backend = self._validated_backend(provider)
        try:
            backend.set_password(self.service, provider, key)
        except Exception:
            raise self._operation_error(provider) from None

    def get_key(self, provider: str) -> str | None:
        """Retrieve a provider key, or ``None`` when no key is registered."""
        provider = self._validate_provider(provider)
        backend = self._validated_backend(provider)
        try:
            value = backend.get_password(self.service, provider)
        except Exception:
            raise self._operation_error(provider) from None
        if value is not None and not isinstance(value, str):
            raise self._operation_error(provider)
        return value

    def delete_key(self, provider: str) -> None:
        """Delete a provider key; deleting an absent key is a no-op."""
        provider = self._validate_provider(provider)
        backend = self._validated_backend(provider)
        try:
            backend.delete_password(self.service, provider)
        except keyring.errors.PasswordDeleteError:
            return
        except Exception:
            raise self._operation_error(provider) from None

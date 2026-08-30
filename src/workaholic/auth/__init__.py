"""Authentication, subjects, credentials, and authorization policy."""

from workaholic.auth.credentials import (
    AvailableCredentialStore,
    CredentialStore,
    HumanCredential,
    select_credential_store,
)
from workaholic.auth.errors import (
    AuthenticationPrimitiveError,
    TokenFormatError,
    TokenGenerationError,
)
from workaholic.auth.file_store import FileCredentialStore
from workaholic.auth.keyring_store import KeyringCredentialStore, KeyringProvider
from workaholic.auth.models import CredentialBackend, ParsedToken, RawToken
from workaholic.auth.sources import (
    ExplicitCredential,
    ExplicitCredentialKind,
    read_token_file,
    resolve_credential_backend,
    resolve_explicit_credential,
)
from workaholic.auth.tokens import (
    generate_token,
    hash_token,
    parse_token,
    verify_token_digest,
)

__all__ = [
    "AuthenticationPrimitiveError",
    "AvailableCredentialStore",
    "CredentialBackend",
    "CredentialStore",
    "ExplicitCredential",
    "ExplicitCredentialKind",
    "FileCredentialStore",
    "HumanCredential",
    "KeyringCredentialStore",
    "KeyringProvider",
    "ParsedToken",
    "RawToken",
    "TokenFormatError",
    "TokenGenerationError",
    "generate_token",
    "hash_token",
    "parse_token",
    "read_token_file",
    "resolve_credential_backend",
    "resolve_explicit_credential",
    "select_credential_store",
    "verify_token_digest",
]

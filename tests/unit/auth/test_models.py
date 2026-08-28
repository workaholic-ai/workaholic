"""Unit tests for secret-safe authentication value objects."""

from __future__ import annotations

import base64
import logging
import traceback
from dataclasses import asdict
from typing import cast

import pytest

from workaholic.auth import ParsedToken, RawToken, TokenFormatError, parse_token
from workaholic.domain import TokenId


def _raw_text() -> str:
    """Return deterministic canonical secret-bearing Token text."""
    encoded = base64.urlsafe_b64encode(b"s" * 32).decode("ascii").rstrip("=")
    return f"tok_secret.{encoded}"


def test_raw_token_exposes_secret_only_through_explicit_accessor() -> None:
    """Common display and formatting paths remain redacted."""
    text = _raw_text()
    raw_token = RawToken(text)

    assert raw_token.get_secret_value() is text
    assert text not in repr(raw_token)
    assert text not in str(raw_token)
    assert text not in f"{raw_token}"
    assert repr(raw_token) == "RawToken(<redacted>)"
    assert str(raw_token) == "<redacted>"
    with pytest.raises(ValueError, match="does not support"):
        format(raw_token, ">20")


def test_parsed_token_repr_and_dataclass_conversion_do_not_render_secret() -> None:
    """Parsed metadata presentation keeps the credential opaque."""
    text = _raw_text()
    parsed = parse_token(text)

    assert parsed.token_id == TokenId("tok_secret")
    assert text not in repr(parsed)
    assert text not in repr(asdict(parsed))
    assert parsed.raw_token.get_secret_value() == text


def test_logs_and_safe_tracebacks_do_not_disclose_raw_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Logging wrappers and parser exceptions cannot echo credential input."""
    text = _raw_text()
    raw_token = RawToken(text)
    with caplog.at_level(logging.INFO):
        logging.getLogger("workaholic.auth.test").info("credential=%s", raw_token)
    assert text not in caplog.text

    malformed = f"{text}.extra"
    try:
        parse_token(malformed)
    except TokenFormatError as error:
        rendered = "".join(traceback.format_exception_only(type(error), error))
    else:  # pragma: no cover - assertion branch documents test intent.
        pytest.fail("Malformed Token unexpectedly parsed.")
    assert malformed not in rendered
    assert text not in rendered


def test_parsed_token_runtime_checks_reject_forged_fields() -> None:
    """ParsedToken validates both fields instead of trusting annotations."""
    raw_token = RawToken(_raw_text())
    with pytest.raises(TokenFormatError):
        ParsedToken(
            token_id=cast("TokenId", "tok_secret"),
            raw_token=raw_token,
        )
    with pytest.raises(TokenFormatError):
        ParsedToken(
            token_id=TokenId("tok_secret"),
            raw_token=cast("RawToken", object()),
        )

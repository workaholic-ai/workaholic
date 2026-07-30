"""Deterministic Human and JSON success rendering."""

from __future__ import annotations

import json

import typer

from workaholic.cli.envelopes import JsonSuccess, JsonValue


def write_success(data: object, *, json_mode: bool) -> None:
    """Write one successful command result to stdout.

    JSON mode emits exactly one compact UTF-8 envelope and one newline. Human
    mode is intentionally non-contractual but deterministic and contains no
    terminal-dependent formatting.

    Args:
        data: Supported command-specific result value.
        json_mode: Whether to emit the public automation envelope.

    Raises:
        TypeError: If ``json_mode`` is not a real boolean or ``data`` has an
            unsupported value type.
        ValueError: If ``data`` cannot be represented as interoperable JSON.

    """
    candidate_json_mode: object = json_mode
    if type(candidate_json_mode) is not bool:
        message = "json_mode must be a boolean."
        raise TypeError(message)
    envelope = JsonSuccess.model_validate({"data": data})
    if candidate_json_mode:
        typer.echo(
            envelope.model_dump_json(
                by_alias=True,
                exclude_none=False,
                ensure_ascii=False,
            )
        )
        return
    typer.echo(_render_human_data(envelope.data))


def _render_human_data(data: JsonValue) -> str:
    """Render one normalized result deterministically for a Human.

    Args:
        data: Validated command-specific JSON value.

    Returns:
        Stable readable text without ANSI styling or terminal inspection.

    """
    if isinstance(data, str):
        return data
    return json.dumps(
        data,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

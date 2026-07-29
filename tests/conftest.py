"""Shared pytest fixtures for real-boundary acceptance specifications."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.golden import GoldenJourneyRunner


@pytest.fixture
def golden_runner() -> GoldenJourneyRunner:
    """Require a real-process journey harness before removing a phase skip."""
    pytest.fail(
        "No golden-journey runner exists in Phase 0. Implement the real process, "
        "storage, and server boundary required by the journey before removing "
        "its phase-specific skip."
    )

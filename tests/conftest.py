"""Pytest configuration for standalone broker tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def socket_enabled() -> None:
    """Enable loopback sockets for tests that opt into network access."""
    yield


@pytest.fixture(autouse=True)
def _enable_socket(socket_enabled: None) -> None:
    """Keep socket opt-in explicit without depending on HA-core plugins."""
    yield

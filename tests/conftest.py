"""Pytest configuration for broker tests."""

from __future__ import annotations

import asyncio
import pytest

# Reset to the default policy so asyncio.get_event_loop() works even when
# pytest_homeassistant_custom_component is installed in the environment.
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


@pytest.fixture(scope="session", autouse=True)
def _ensure_event_loop():
    """Ensure a base event loop exists for plugins that expect one."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            yield
        finally:
            loop.close()
    else:
        yield


@pytest.fixture(autouse=True)
def enable_event_loop_debug():
    """Override HA plugin fixture that requires a hass event loop."""
    yield


@pytest.fixture(autouse=True)
def verify_cleanup():
    """Override HA plugin cleanup fixture to avoid hass loop dependency."""
    yield


@pytest.fixture(autouse=True)
def _enable_socket(socket_enabled):
    """Allow network access for tests requiring sockets."""
    yield

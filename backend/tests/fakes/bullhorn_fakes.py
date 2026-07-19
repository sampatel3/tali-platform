"""Fixtures + helpers to drive the fake Bullhorn server two ways.

The real Bullhorn client (``components/integrations/bullhorn/service.py``, PR-3)
mirrors the Workable client: it constructs a fresh ``httpx.Client()`` per call
against ABSOLUTE urls returned by discovery — it does NOT accept an injected
transport. So contract/E2E tests that drive the real client need a live socket;
use :func:`live_bullhorn_server` and point the org's discovery url at
``server.discovery_url``.

Tests that want to poke the fake directly (no real client) can skip the socket
and use :func:`asgi_client` for an in-process httpx client over ASGITransport —
faster, no port.

Both are plain helpers plus pytest fixtures, following the tests/ convention of
function-scoped fixtures that build fresh state per test.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from dataclasses import dataclass
from typing import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient

from .bullhorn_app import build_app
from .bullhorn_state import FakeBullhornState


# --- in-process (ASGI) form -------------------------------------------------


@contextlib.contextmanager
def asgi_client(
    state: FakeBullhornState | None = None,
) -> Iterator[tuple[TestClient, FakeBullhornState]]:
    """Yield ``(TestClient, state)`` talking to the fake in-process.

    Uses Starlette's ``TestClient`` (the house convention in ``conftest.py``) so
    the sync test can drive the ASGI app path-routed, with no socket. Its
    ``.get/.post/.put/.delete`` mirror the ``httpx`` client the real integration
    uses, so contract tests read the same. Good for asserting on state/counters.
    """
    app = build_app(state)
    with TestClient(app) as client:
        yield client, app.state.bh


# --- live-socket (uvicorn) form ---------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LiveServer:
    base_url: str
    state: FakeBullhornState
    _server: uvicorn.Server
    _thread: threading.Thread

    @property
    def discovery_url(self) -> str:
        """The loginInfo endpoint — hand this to the client as its discovery url."""
        return f"{self.base_url}/rest-services/loginInfo"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@contextlib.contextmanager
def live_bullhorn_server(state: FakeBullhornState | None = None) -> Iterator[LiveServer]:
    """Boot the fake on an ephemeral 127.0.0.1 port via uvicorn in a thread.

    Deterministic w.r.t. the fake's own logic (test clock, counters); only the
    transport is real. Use for the actual Bullhorn client, whose internal
    ``httpx.Client()`` needs a reachable socket.
    """
    app = build_app(state)
    port = _free_port()
    # Keep the test server on Uvicorn's portable implementations. Its optional
    # native loop/protocol auto-selection can block the entire pytest process
    # while importing an optional extension in a background thread, preventing
    # even the bounded startup deadline below from running. The fake does not
    # benchmark the HTTP stack or expose WebSockets, so asyncio+h11 with WS
    # disabled exercises the same socket contract deterministically.
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="asyncio",
        http="h11",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for the server loop to accept connections (bounded, deterministic-ish).
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:  # pragma: no cover
        raise RuntimeError("fake Bullhorn server failed to start")
    live = LiveServer(
        base_url=f"http://127.0.0.1:{port}",
        state=app.state.bh,
        _server=server,
        _thread=thread,
    )
    try:
        yield live
    finally:
        live.stop()


# --- pytest fixtures --------------------------------------------------------


@pytest.fixture
def bullhorn_state() -> FakeBullhornState:
    """A fresh, empty fake-Bullhorn state per test."""
    return FakeBullhornState()


@pytest.fixture
def bullhorn_asgi(bullhorn_state: FakeBullhornState) -> Iterator[TestClient]:
    """In-process TestClient over the fake (path-routed, no socket)."""
    with asgi_client(bullhorn_state) as (client, _state):
        yield client


@pytest.fixture
def bullhorn_live(bullhorn_state: FakeBullhornState) -> Iterator[LiveServer]:
    """Live uvicorn-backed fake on an ephemeral port (for the real client)."""
    with live_bullhorn_server(bullhorn_state) as server:
        yield server

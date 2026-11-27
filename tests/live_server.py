"""A real uvicorn server in a thread, for the tests that need a client to go away.

``TestClient`` runs the app inside the test and never closes a stream from the
client's side: a test that stops reading leaves the response running to its end, so
whatever a real disconnect does to a turn never happens there. A disconnect is a
closed socket, which only a server sees. The tests about one serve the app here and
drop the connection with :func:`post_then_drop`.

The server runs in the test's own process, so the store fixture, ``monkeypatch`` and
``threading.Event`` gates all reach the code it serves.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import uvicorn


class LiveServer:
    """Serve ``app`` on a free loopback port between :meth:`start` and :meth:`stop`."""

    def __init__(self, app, *, graceful_shutdown_s: float = 2.0) -> None:
        # Bound here rather than by uvicorn, so the port is known before the server
        # starts and no other test can take it in between.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        config = uvicorn.Config(
            app,
            lifespan="on",
            log_level="warning",
            timeout_graceful_shutdown=graceful_shutdown_s,
        )
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self._sock]}, daemon=True
        )

    def start(self) -> LiveServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("the live server did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        """Ask the server to shut down, as a deploy's SIGTERM would, and wait for it."""
        self.server.should_exit = True
        self._thread.join(timeout=15)
        self._sock.close()

    @property
    def stopped(self) -> bool:
        return not self._thread.is_alive()

    def __enter__(self) -> LiveServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        if not self.stopped:
            self.stop()


def post_then_drop(
    port: int,
    path: str,
    body: dict,
    *,
    drop_when: threading.Event | None = None,
    timeout: float = 10.0,
) -> bytes:
    """POST ``body`` as JSON, read up to the first SSE ``data:`` line, then hang up.

    With ``drop_when``, the socket is closed only once that event is set, so a test
    decides what the server is doing when its client goes away. Returns what was read.
    """
    payload = json.dumps(body).encode()
    head = (
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\nAccept: text/event-stream\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode()
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(head + payload)
        received = b""
        deadline = time.monotonic() + timeout
        while b"data:" not in received:
            if time.monotonic() > deadline:
                raise AssertionError(f"no event arrived before the drop; read {received!r}")
            chunk = sock.recv(65536)
            if not chunk:
                raise AssertionError(f"the server closed the stream first; read {received!r}")
            received += chunk
        if drop_when is not None and not drop_when.wait(timeout):
            raise AssertionError("the moment to drop the connection never came")
        sock.shutdown(socket.SHUT_RDWR)
    finally:
        sock.close()
    return received

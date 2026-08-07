"""Shared SSE response headers.

Streaming chat/generation deltas only feel live if no intermediary buffers the
response body. ``sse_starlette`` already sets ``X-Accel-Buffering: no`` (the
nginx convention), but we set these explicitly so the behaviour is independent
of the library default, and so any nginx-family proxy in front disables
buffering. NOTE: Caddy ignores ``X-Accel-Buffering`` — a Caddy reverse proxy
(the bundled one and any external edge) must use ``flush_interval -1`` on the
SSE arm; see ``infra/proxy/README.md``.
"""

from __future__ import annotations

# Passed as ``EventSourceResponse(..., headers=SSE_HEADERS)``.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

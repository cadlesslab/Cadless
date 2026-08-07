"""Loopback-bind guard.

The /settings endpoint stores API keys unauthenticated, so the host-facing
surfaces must bind 127.0.0.1 only. These are source-level assertions (no server
import) that fail if a future edit reintroduces a 0.0.0.0 host binding.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_dev_entrypoint_binds_loopback():
    src = (_ROOT / "backend" / "main.py").read_text()
    assert 'host="127.0.0.1"' in src
    assert 'host="0.0.0.0"' not in src  # the bind directive, not the explanatory comment


def test_published_proxy_port_binds_loopback():
    compose = (_ROOT / "docker-compose.yml").read_text()
    assert "127.0.0.1:${CADLESS_PROXY_PORT" in compose

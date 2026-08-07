"""uvicorn entrypoint: `python -m backend.main` or `uvicorn backend.main:app`."""

from __future__ import annotations

from backend.app import create_app
from cadless import user_settings
from cadless.config import settings

# Apply saved runtime settings (provider/keys/models) before the app is built,
# honouring env > saved > default precedence.
user_settings.apply_startup()

app = create_app()


def main() -> None:
    import uvicorn

    # Loopback only: the /settings endpoint stores API keys unauthenticated.
    # The Docker api container binds 0.0.0.0 internally and is reached via
    # the proxy, whose published host port is pinned to 127.0.0.1.
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "settings"]

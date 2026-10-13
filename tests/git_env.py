"""The environment a test runs git in when the repository is its own scratch one.

Run from inside a git hook, a test inherits GIT_DIR, GIT_INDEX_FILE and the
like, which point git back at the real repository; global and system config can
add hooks or signing to a commit. Neither belongs to a scratch repository.
"""

from __future__ import annotations

import os


def isolated_git_env(**extra: str) -> dict[str, str]:
    """The current environment without GIT_* variables, reading no global or system config."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **extra}

"""The served subpath is one build parameter, not three literals that agree.

The bundle's base URL, the directory the bundle is copied into, and the nginx
`location` that serves it must all name the same prefix. Only the first was
ever a build argument; the other two were literals, so building the same tree
for a different subpath produced an image that starts, serves an index, and
404s every asset that index references. Nothing fails loudly, which is why the
derivation is pinned here rather than left to review.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = (_ROOT / "frontend" / "Dockerfile").read_text()
_NGINX = (_ROOT / "infra" / "nginx.conf").read_text()
_COMPOSE = (_ROOT / "docker-compose.yml").read_text()
_DEFAULT_PREFIX = "/apps/cadless/"


def _serve_stage() -> str:
    """The final stage — the one that assembles the nginx image."""
    return "FROM " + _DOCKERFILE.split("\nFROM ")[-1]


def _directives() -> str:
    """nginx.conf without comments; a comment cannot misroute a request."""
    return "\n".join(line for line in _NGINX.splitlines() if not line.lstrip().startswith("#"))


def test_the_bundle_lands_where_the_parameter_says():
    copy = re.search(r"^COPY --from=build /app/dist (\S+)", _serve_stage(), re.MULTILINE)
    assert copy, "the serve stage no longer copies the bundle"
    assert "${BASE_PATH}" in copy.group(1), copy.group(1)


def test_the_serve_stage_declares_the_parameter_it_uses():
    # An ARG does not cross a stage boundary. Drop this line and ${BASE_PATH}
    # expands to the empty string, putting the bundle at the server root: the
    # image still builds and still runs, and every asset URL is wrong.
    assert re.search(r"^ARG BASE_PATH=", _serve_stage(), re.MULTILINE)


def test_the_nginx_config_is_rewritten_from_the_same_parameter():
    assert re.search(
        r"sed -i .*\$\{BASE_PATH\}.*/etc/nginx/conf\.d/default\.conf",
        _serve_stage(),
    )


def test_a_prefix_without_a_trailing_slash_is_refused():
    # The replaced text ends in a slash, so a BASE_PATH without one splices the
    # fallback into `/apps/fooindex.html`. The image would still build, push and
    # start; only the page would be wrong. Refused at build time instead.
    assert re.search(r'case "\$\{BASE_PATH\}" in', _serve_stage())
    assert "exit 1" in _serve_stage()


def test_every_served_prefix_is_one_the_substitution_rewrites():
    # The substitution matches the trailing-slash spelling. A `location` added
    # later without it would survive untouched and keep serving the default
    # prefix while the bundle moved, which is the same silent half-break.
    for hit in re.findall(r"/apps/cadless\S*", _directives()):
        assert hit.startswith(_DEFAULT_PREFIX), hit


def test_the_api_base_is_derived_rather_than_written_again():
    # A second literal could be left behind when the prefix moved, and the
    # result is worse than a broken page: the app loads and posts the user's
    # API key to the old prefix, which on a shared host is somebody else's.
    assert re.search(r"^ARG VITE_API_BASE=\$\{BASE_PATH\}", _DOCKERFILE, re.MULTILINE)
    assert "ARG VITE_API_BASE=/apps/" not in _DOCKERFILE
    # An explicit build arg overrides the derived default, so the compose file
    # could quietly reinstate the second literal the Dockerfile just removed.
    assert "VITE_API_BASE" not in _COMPOSE


def test_sed_metacharacters_in_the_prefix_are_refused():
    # In a sed replacement `&` expands to the whole match, `|` is this
    # expression's delimiter and `\` escapes — each quietly rewrites the config
    # into something other than what was asked for, and still builds.
    assert re.search(r"\*\[\\\\\\&\\\|\]\*\)", _serve_stage())


def test_the_default_is_written_down_once_per_place_that_needs_it():
    defaults = re.findall(r"^ARG BASE_PATH=(\S+)", _DOCKERFILE, re.MULTILINE)
    assert len(defaults) == 2, "both stages declare the parameter"
    assert set(defaults) == {_DEFAULT_PREFIX}, defaults
    assert _DEFAULT_PREFIX in _directives()

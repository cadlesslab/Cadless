"""A stand-in for `gh api` over one repository's releases, run as a script.

Called as `fake_github.py STATE ARGS...`: STATE is a directory holding
`releases.json`, a list of releases each with its `id`, `tag_name`, `draft` and
`assets` (each a `name` and a `digest`), and `token`, the one GH_TOKEN it
accepts; ARGS are what a step passed to gh. It answers the release list and one
release's assets, applying a `--jq` filter with jq as gh applies it with its own;
it attaches an upload under its bytes' digest — or under the digest of
`stored-bytes`, when STATE holds that file, as a store that kept other bytes
would — refusing a name already attached and a release no longer a draft, as
GitHub does with immutable releases on; it applies a PATCH's typed fields. Every
call is appended to `calls.log`, and a call without the token, or one it does
not know, fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RELEASES = "repos/o/r/releases"
UPLOADS = "https://uploads.github.com/repos/o/r/releases"


def _answer(data: object, jq: str | None) -> int:
    if jq is None:
        print(json.dumps(data))
        return 0
    result = subprocess.run(["jq", "-r", jq], input=json.dumps(data), text=True, check=False)
    return result.returncode


def _refuse(why: str) -> int:
    print(f"gh: {why}", file=sys.stderr)
    return 1


def main(state: Path, args: list[str]) -> int:
    with (state / "calls.log").open("a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\n")
    if os.environ.get("GH_TOKEN") != (state / "token").read_text(encoding="utf-8"):
        return _refuse("no token, or not the one this repository accepts")
    if not args or args[0] != "api":
        return _refuse(f"unexpected call: {args}")
    method, jq, body, endpoint, fields = "GET", None, None, "", {}
    rest = iter(args[1:])
    for arg in rest:
        if arg == "--method":
            method = next(rest)
        elif arg == "--jq":
            jq = next(rest)
        elif arg == "--input":
            body = Path(next(rest)).read_bytes()
        elif arg == "-H":
            next(rest)
        elif arg == "-F":
            key, _, value = next(rest).partition("=")
            fields[key] = json.loads(value)
        elif arg in ("--paginate", "--silent"):
            continue
        elif arg.startswith("-"):
            return _refuse(f"unknown flag {arg}")
        else:
            endpoint = arg

    path = state / "releases.json"
    releases = json.loads(path.read_text(encoding="utf-8"))
    by_id = {str(release["id"]): release for release in releases}

    if method == "GET" and endpoint == RELEASES:
        return _answer(releases, jq)
    if method == "GET" and (found := re.fullmatch(rf"{RELEASES}/(\d+)/assets", endpoint)):
        release = by_id.get(found[1])
        return _answer(release["assets"], jq) if release else _refuse("HTTP 404")
    if method == "POST" and (
        found := re.fullmatch(rf"{re.escape(UPLOADS)}/(\d+)/assets\?name=([\w.-]+)", endpoint)
    ):
        release = by_id.get(found[1])
        if release is None or body is None:
            return _refuse("HTTP 404")
        if not release["draft"] or any(a["name"] == found[2] for a in release["assets"]):
            return _refuse("HTTP 422")
        stored = state / "stored-bytes"
        digest = (
            "sha256:" + hashlib.sha256(stored.read_bytes() if stored.exists() else body).hexdigest()
        )
        release["assets"].append({"name": found[2], "digest": digest})
    elif method == "PATCH" and (found := re.fullmatch(rf"{RELEASES}/(\d+)", endpoint)):
        if found[1] not in by_id:
            return _refuse("HTTP 404")
        by_id[found[1]].update(fields)
    else:
        return _refuse(f"unexpected call: {method} {endpoint}")
    path.write_text(json.dumps(releases), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2:]))

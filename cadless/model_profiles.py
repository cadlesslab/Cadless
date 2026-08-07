"""Model slug -> AWS Bedrock inference-profile ID mapping.

Mirrors the-Engine-v2 pattern: callers use short slugs; the runtime
Bedrock IDs are resolved here at startup so a misconfigured slug raises KeyError
immediately rather than 404-ing the first Converse call.

**The IDs below are verified present in this account/region** via
`aws bedrock list-inference-profiles --region us-east-1` (account 033040503723,
2026-06-07). Do not add a slug without confirming the resolved ID accepts a
Converse call.
"""

from __future__ import annotations

# slug -> bedrock cross-region inference-profile ID
PROFILES: dict[str, str] = {
    "sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "sonnet-4-5": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "opus-4-8": "us.anthropic.claude-opus-4-8",
    "opus-4-7": "us.anthropic.claude-opus-4-7",
    "opus-4-6": "us.anthropic.claude-opus-4-6-v1",
}


def resolve_model_id(model_slug: str) -> str:
    """Return the Bedrock inference-profile ID for `model_slug`.

    Raises KeyError immediately on an unknown slug (fail fast at startup).
    """
    return PROFILES[model_slug]

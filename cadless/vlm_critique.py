"""Optional VLM render-critique repair signal — OFF by default.

After a model executes successfully, render it to an image and ask a vision model
whether it matches the request. A "mismatch" verdict becomes an extra repair
signal in the pipeline (error-only repair catches code that *crashes*; this
catches code that *runs but builds the wrong shape*).

The image renderer is **pluggable** because headless B-Rep->PNG rendering needs a
GL/offscreen stack we don't pull in for the PoC. Inject a renderer (e.g. one
backed by trimesh+pyrender or OCCT offscreen) to enable it for real; without one,
the critic is inert.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cadless.config import Settings, settings

# A renderer maps a GLB file path to PNG image bytes.
Renderer = Callable[[str], bytes]


@dataclass
class Critique:
    matches: bool
    feedback: str


class VlmCritic:
    def __init__(
        self,
        renderer: Renderer,
        client=None,
        config: Settings | None = None,
    ):
        self._render = renderer
        self._cfg = config or settings
        self._client = client  # boto3 bedrock-runtime; lazy if None

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self._cfg.aws_region)
        return self._client

    def critique(self, intent: str, glb_path: str) -> Critique:
        png = self._render(glb_path)
        question = (
            f"This is a render of a CAD part generated for the request:\n"
            f'"{intent}"\n\n'
            f"Does the geometry match the request? Reply with exactly 'MATCH' if it "
            f"does, or 'MISMATCH: <what is wrong>' if it does not."
        )
        resp = self.client.converse(
            modelId=self._cfg.vlm_model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": "png", "source": {"bytes": png}}},
                        {"text": question},
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 300, "temperature": 0.0},
        )
        text = "".join(p.get("text", "") for p in resp["output"]["message"]["content"]).strip()
        return parse_verdict(text)


def parse_verdict(text: str) -> Critique:
    stripped = text.strip()
    if stripped.upper().startswith("MATCH"):
        return Critique(matches=True, feedback="")
    feedback = stripped
    if ":" in stripped:
        feedback = stripped.split(":", 1)[1].strip()
    return Critique(matches=False, feedback=feedback or "model does not match the request")

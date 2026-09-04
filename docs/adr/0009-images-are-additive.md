# ADR-0009: Images are additive on the seam — but a provider that cannot see is refused, not skipped

## Status

Accepted. Extends [ADR-0001](./0001-provider-seam.md) with a new neutral block
kind, and deliberately inverts the contract [ADR-0002](./0002-embeddings-are-additive.md)
set for a capability gap.

## Context

A reference image is one of the plainest ways to ask for a part: the user has a
picture of the thing and wants it built. The seam had no way to carry one —
`BlockKind` was a closed union of six text-shaped kinds — so the only vision in
the tree was `cadless/vlm_critique.py`, which calls `boto3` directly and is
therefore invisible to the registry and locked to one vendor.

Every vendor spells an image differently: Converse takes raw bytes plus a format
token, the Messages API takes a base64 source with a media type, and OpenAI takes
a data URL inside an array content form. That is exactly the shape ADR-0001 says
belongs behind the seam rather than in engine code.

The harder question was what to do about a model that cannot read one. ADR-0002
answered the same question for embeddings with "skip quietly", and copying that
answer here would have been the obvious move.

## Decision

- **`image` is a member of `BlockKind`, carried neutrally.** A `ContentBlock`
  holds the payload as base64 plus its `media_type`; each adapter reshapes those
  into its own wire form. The engine never sees a vendor's image encoding.
- **`Capabilities.supports_images` defaults to `False`.** An adapter installed
  beside the engine ([ADR-0008](./0008-provider-entry-point.md)) names the fields
  it knows about, and one written before this field existed must read as "cannot
  see" rather than claim a capability nobody checked.
- **A turn carrying an image is refused when the model cannot read one**, at the
  request boundary, with the typed `ImagesUnsupported` as the backstop for paths
  that did not check. This is the inversion: embeddings were something the engine
  wanted, so skipping them degrades a feature; an image is something the user
  handed over, so skipping it means presenting a part built from a picture the
  model never saw, with nothing on screen to say so.
- **The refusal happens before the turn starts.** An exception raised inside a
  running turn is converted to an error event *and reverts the project to its
  last good version* — a destructive way to report a rejected attachment.
- **`complete()` is not widened.** It takes a bare string, and code outside this
  tree implements that exact signature. A call carrying an image goes down the
  message-based path instead, which every provider already implements. That
  applies to every codegen call of a turn, not only the first: refine, repair and
  each best-of-N candidate carry the picture too, because they are correcting the
  shape the picture describes.
- **The bytes are scoped to the turn that carried them.** What travels forward is
  a written reading of the image, extracted from the codegen reply and stored
  beside the block. Later turns are given that instead of the pixels.

## Consequences

- Adding a provider now includes an image branch and an honest
  `supports_images`. A provider that omits both still works for every text turn
  and refuses attachments cleanly, so the seam stays additive.
- A user on a vision-less model gets a sentence telling them so, rather than a
  part that quietly ignored their picture. The cost is that they cannot attach
  one at all until they switch models — which is the honest state of affairs.
- Carrying the reading rather than the pixels costs roughly a quarter of what
  re-sending the image would over a long session, and loses fidelity: it serves
  "make it twice as tall" and not "the fillet on the left bracket is wrong".
- A turn that attaches an image but runs no codegen leaves no reading, so a
  later turn has no reference. That is accepted rather than solved here.
- `vlm_critique.py` still calls Bedrock directly. This ADR gives it somewhere to
  move to; moving it is separate work.

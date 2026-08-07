/** Says that what is in the viewport is not on this machine, and ends it.
 *
 * A catalogue item drawn here before it is fetched looks exactly like one that
 * was fetched — same viewport, same orbit, same readout — and everything else
 * on screen still describes the project open behind it. Without something that
 * says so,
 * the only way to find out which one you are looking at is to try to change it.
 *
 * Outside the Canvas rather than in it: this is a sentence and a button, and
 * putting them in the scene would mean drawing text in a 3D view to say the 3D
 * view is not yours. */
import { useViewport, viewportStore } from "./viewportStore";

export function PreviewBanner() {
  const preview = useViewport((s) => s.preview);
  if (!preview) return null;

  return (
    <div className="vp-preview" role="status">
      <span className="vp-preview-what">{preview.title}</span>
      <span className="vp-preview-where">Not on this machine yet</span>
      <button className="vp-preview-exit" onClick={() => viewportStore.clearPreview()}>
        Stop previewing
      </button>
    </div>
  );
}

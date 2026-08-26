/** Details inspector: what this version is — status, metrics, and the code that
 * made it.
 *
 * Getting something *out* of it used to be here too, which is how the formats,
 * Print and Share came to live behind a tab called "Details". They are their own
 * panel now; this one answers what you are looking at rather than what you can
 * do with it. */
import { EmptyState, Panel } from "../components";
import { useActiveVersion } from "../state";
import { CodePanel } from "./CodePanel";

export function Inspector() {
  const version = useActiveVersion();

  if (!version) {
    return (
      <Panel title="Details">
        <EmptyState>Select or generate a version.</EmptyState>
      </Panel>
    );
  }

  return (
    <Panel title="Details">
      <div className="details">
        <div className="detail-row">
          <span className={`status ${version.ok ? "ok" : "bad"}`}>
            {version.ok ? "✅ ok" : "⚠ failed"}
          </span>
          {version.volume != null && (
            <span className="metric">{version.volume.toFixed(1)} mm³</span>
          )}
          {version.bbox && (
            <span className="metric">
              {version.bbox.map((d) => d.toFixed(1)).join(" × ")} mm
            </span>
          )}
        </div>

        {version.error && <p className="detail-error">{version.error}</p>}

        {version.code && <CodePanel code={version.code} error={version.error} />}
      </div>
    </Panel>
  );
}

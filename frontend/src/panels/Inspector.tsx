/** Details inspector: what this version is — status, metrics, its parts, and the
 * code that made it.
 *
 * Getting something *out* of it used to be here too, which is how the formats,
 * Print and Share came to live behind a tab called "Details". They are their own
 * panel now; this one answers what you are looking at rather than what you can
 * do with it. */
import { EmptyState, Panel } from "../components";
import { measureText } from "../measures";
import { useActiveVersion } from "../state";
import { CodePanel } from "./CodePanel";
import { PartList } from "./PartList";

export function Inspector() {
  const version = useActiveVersion();

  if (!version) {
    return (
      <Panel title="Details">
        <EmptyState>Select or generate a version.</EmptyState>
      </Panel>
    );
  }

  const measures = measureText(version.volume, version.bbox);
  return (
    <Panel title="Details">
      <div className="details">
        <div className="detail-row">
          <span className={`status ${version.ok ? "ok" : "bad"}`}>
            {version.ok ? "✅ ok" : "⚠ failed"}
          </span>
          {measures.volume && <span className="metric">{measures.volume}</span>}
          {measures.size && (
            <span className="metric">
              {measures.size} {measures.unit}
            </span>
          )}
        </div>

        {version.error && <p className="detail-error">{version.error}</p>}

        <PartList version={version} />

        {version.code && <CodePanel code={version.code} error={version.error} />}
      </div>
    </Panel>
  );
}

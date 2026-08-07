/** Details inspector: status, metrics, code, downloads (foundation).
 * Code highlighting and the export picker extend this. */
import { EmptyState, Panel } from "../components";
import { useActiveVersion } from "../state";
import { CodePanel } from "./CodePanel";
import { ExportShare } from "./ExportShare";

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

        {version.ok && <ExportShare version={version} />}

        {version.code && <CodePanel code={version.code} error={version.error} />}
      </div>
    </Panel>
  );
}

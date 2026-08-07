/** Parameters flyout: editable parameter sliders for the active version.
 *
 * Catalog items are read-only: their parameters are shown as static
 * values, and a "Customize" action (#22) clones the item into an editable copy. */
import { Button, EmptyState, Panel } from "../components";
import { useActiveProject, useActiveVersion } from "../state";
import { useApp } from "../useApp";
import { ParameterInspector } from "./ParameterInspector";

export function ParametersPanel() {
  const app = useApp();
  const version = useActiveVersion();
  const project = useActiveProject();
  const isCatalog = project?.is_catalog ?? false;
  const numericCount = version
    ? Object.values(version.parameters).filter((v) => typeof v === "number").length
    : 0;

  if (!version?.ok || numericCount === 0) {
    return (
      <Panel title="Parameters">
        <EmptyState>
          {version?.ok
            ? "This version has no editable parameters."
            : "Generate a part to edit parameters."}
        </EmptyState>
      </Panel>
    );
  }

  if (isCatalog) {
    return (
      <Panel title="Parameters">
        <div className="params-readonly-note">
          <p>Catalog items are read-only. Customize this item to edit its parameters.</p>
          <Button
            onClick={() =>
              project && void app.cloneCatalogItem(project.id, `${project.name} (copy)`)
            }
          >
            Customize
          </Button>
        </div>
        <ParameterInspector readOnly />
      </Panel>
    );
  }

  return (
    <Panel title="Parameters">
      <ParameterInspector />
    </Panel>
  );
}

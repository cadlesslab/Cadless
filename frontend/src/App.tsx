/** App shell + bootstrap. A fixed left activity rail opens floating
 * panels over a full-width viewport; the chat panel sits on the right
 * (resizable + collapsible). */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { IconButton } from "./components";
import { ResizeHandle } from "./components/ResizeHandle";
import { LeftRail, RailFlyout, type PanelId } from "./panels/LeftRail";
import { ChatPanel } from "./panels/ChatPanel";
import { projectIdFromPath, syncProjectUrl } from "./routing";
import { useStoreSelector } from "./state";
import { usePanelLayout } from "./usePanelLayout";
import { useApp } from "./useApp";
import { Viewport } from "./viewport/Viewport";

export function App() {
  const app = useApp();
  const layout = usePanelLayout();
  const [active, setActive] = useState<PanelId | null>(null);
  const booted = useRef(false);
  const activeProjectId = useStoreSelector((s) => s.activeProjectId);

  useEffect(() => {
    if (booted.current) return; // guard React StrictMode double-invoke
    booted.current = true;
    // Deep link by project id in the path: /apps/cadless/<id>. Falls
    // back to the legacy ?project=&version= share link, then to normal startup.
    const pathId = projectIdFromPath();
    const params = new URLSearchParams(window.location.search);
    const version = params.get("version") ?? params.get("v");
    const versionId = version ? Number(version) : undefined;
    const queryProject = params.get("project");
    if (pathId != null) void app.openShared(pathId, versionId);
    else if (queryProject) void app.openShared(Number(queryProject), versionId);
    else void app.bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reflect the active project in the address bar so it stays deep-linkable.
  useEffect(() => {
    syncProjectUrl(activeProjectId);
  }, [activeProjectId]);

  const style = { "--right-w": `${layout.rightWidth}px` } as CSSProperties;

  return (
    <div className="app">
      <LeftRail active={active} onSelect={(id) => setActive((cur) => (cur === id ? null : id))} />

      <div className="body" style={style}>
        <section className="work" aria-label="Workspace">
          <Viewport />
        </section>

        {!layout.rightCollapsed && (
          <ResizeHandle
            label="Resize chat panel"
            direction="right"
            width={layout.rightWidth}
            onResize={layout.setRightWidth}
          />
        )}
        <aside className={`side-col right ${layout.rightCollapsed ? "collapsed" : ""}`}>
          {layout.rightCollapsed ? (
            <div className="rail-collapsed">
              <IconButton label="Expand chat" onClick={layout.toggleRight}>
                ⟨
              </IconButton>
              <span className="rail-collapsed-label">Cadless</span>
            </div>
          ) : (
            <ChatPanel onCollapse={layout.toggleRight} />
          )}
        </aside>

        {active && (
          <RailFlyout
            id={active}
            width={layout.leftWidth}
            onResize={layout.setLeftWidth}
            onClose={() => setActive(null)}
          />
        )}
      </div>
    </div>
  );
}

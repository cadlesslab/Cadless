/** App shell + bootstrap. A fixed left activity rail opens floating
 * panels over a full-width viewport; the chat panel sits on the right
 * (resizable + collapsible).
 *
 * Below `useNarrow`'s breakpoint there is no room for a second column, so the
 * chat stops being one and becomes a drawer over the viewport — the same move
 * the rail's flyout already makes on the other side. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { IconButton } from "./components";
import { ResizeHandle } from "./components/ResizeHandle";
import { LeftRail, RailFlyout, type PanelId } from "./panels/LeftRail";
import { ChatPanel } from "./panels/ChatPanel";
import { projectIdFromPath, syncProjectUrl } from "./routing";
import { useStoreSelector } from "./state";
import { useNarrow } from "./useNarrow";
import { usePanelLayout } from "./usePanelLayout";
import { useApp } from "./useApp";
import { Viewport } from "./viewport/Viewport";

export function App() {
  const app = useApp();
  const layout = usePanelLayout();
  const narrow = useNarrow();
  // Deliberately not `layout.rightCollapsed`. That one is persisted, so driving
  // the drawer through it would let a phone leave the chat collapsed the next
  // time the same account opens the app on a laptop.
  const [drawerOpen, setDrawerOpen] = useState(false);
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
  // The collapsed strip is a way to give the viewport more width, which is
  // exactly what the drawer already does. Offering both at once would leave two
  // controls doing one job, so the strip is a wide-layout thing only.
  const collapsed = !narrow && layout.rightCollapsed;
  const classes = ["side-col", "right", narrow && "drawer", collapsed && "collapsed"];

  return (
    <div className="app">
      <LeftRail active={active} onSelect={(id) => setActive((cur) => (cur === id ? null : id))} />

      <div className="body" style={style}>
        <section className="work" aria-label="Workspace">
          <Viewport />
        </section>

        {!narrow && !layout.rightCollapsed && (
          <ResizeHandle
            label="Resize chat panel"
            direction="right"
            width={layout.rightWidth}
            onResize={layout.setRightWidth}
          />
        )}
        {/* Hidden rather than unmounted: the composer's draft is the chat
            panel's own state, so unmounting would throw away a half-typed
            prompt every time the drawer closed. */}
        <aside className={classes.filter(Boolean).join(" ")} hidden={narrow && !drawerOpen}>
          {collapsed ? (
            <div className="rail-collapsed">
              <IconButton label="Expand chat" onClick={layout.toggleRight}>
                ⟨
              </IconButton>
              <span className="rail-collapsed-label">Cadless</span>
            </div>
          ) : (
            <ChatPanel onCollapse={narrow ? () => setDrawerOpen(false) : layout.toggleRight} />
          )}
        </aside>

        {narrow && !drawerOpen && (
          <IconButton className="chat-fab" label="Open chat" onClick={() => setDrawerOpen(true)}>
            ⟨
          </IconButton>
        )}

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

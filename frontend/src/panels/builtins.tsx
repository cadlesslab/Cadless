/** The panels this tree ships, registered at module load.
 *
 * Kept apart from `registry.ts`, which is the mechanism, and from `LeftRail`,
 * which draws whatever the mechanism holds. This file is the one place that
 * names them, so a build that ships a different set differs from this one by
 * this file alone — and a build that only *adds* one does not touch it at all.
 *
 * Imported for its side effect. Anything rendering the rail pulls it in, which
 * is why `LeftRail` does the importing rather than the app root: a test that
 * renders the rail on its own gets the same panels the app does.
 */
import {
  CatalogIcon,
  CubeIcon,
  FolderIcon,
  HistoryIcon,
  ImportIcon,
  InfoIcon,
  SettingsIcon,
  SlidersIcon,
} from "../components";
import { CatalogPanel } from "./CatalogPanel";
import { ImportPanel } from "./ImportPanel";
import { Inspector } from "./Inspector";
import { ParametersPanel } from "./ParametersPanel";
import { ProjectsPanel } from "./ProjectsPanel";
import { registerPanel } from "./registry";
import { SettingsPanel } from "./SettingsPanel";
import { VersionsPanel } from "./VersionsPanel";
import { ViewPanel } from "./ViewPanel";

registerPanel("projects", {
  label: "Projects",
  icon: <FolderIcon />,
  render: () => <ProjectsPanel />,
});
registerPanel("catalog", {
  label: "Catalog",
  icon: <CatalogIcon />,
  render: () => <CatalogPanel />,
});
registerPanel("import", {
  label: "Import",
  icon: <ImportIcon />,
  render: () => <ImportPanel />,
});
registerPanel("view", { label: "View", icon: <CubeIcon />, render: () => <ViewPanel /> });
registerPanel("details", { label: "Details", icon: <InfoIcon />, render: () => <Inspector /> });
registerPanel("parameters", {
  label: "Parameters",
  icon: <SlidersIcon />,
  render: () => <ParametersPanel />,
});
registerPanel("history", {
  label: "History",
  icon: <HistoryIcon />,
  render: () => <VersionsPanel />,
});
registerPanel("settings", {
  label: "Settings",
  icon: <SettingsIcon />,
  render: () => <SettingsPanel />,
});

// `ImportPanel` has a rail entry of its own. It was once drawn inside a panel
// that fetched packages from a service, and was held back from the rail while
// that panel drew it, because registering it as well would have put the same
// control on screen twice.
//
// That panel ships elsewhere now and this entry is what remains, which is the
// point of it: receiving a package someone handed over needs no account, so it
// was never right for it to be a section of a panel that did.

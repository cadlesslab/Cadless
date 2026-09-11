/** The contract, exercised the way a build outside this repository uses it.
 *
 * `plugin.ts` opens by saying it **is** the contract, and until now nothing
 * tested that claim: no test imported the file at all. Dropping an export
 * therefore broke nothing here — it broke a build in another repository, at
 * its typecheck, with no hint pointing back at the commit that did it.
 *
 * What is pinned here is not that these names exist as strings. It is that a
 * panel can be *assembled* from them: find what is open, hand it to the export
 * UI, and get something a reader can act on. The names, the types and the way
 * they compose all fail together if any one of them leaves.
 *
 * The mocks and `renderWithProviders` are this test standing in for the app —
 * a plugin is rendered *inside* a build, and the providers are exactly what
 * `plugin.ts` deliberately withholds. Everything the panel itself touches
 * comes from `./plugin` and nowhere else, which is the restriction
 * `.eslintrc.cjs` already puts on a real plugin's source.
 */
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as api from "./api";
import type { PrintCapability } from "./api";
import { renderWithProviders } from "./test/utils";

import { ExportShare, type Version, useActiveVersion } from "./plugin";

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  fetchPrintCapability: vi.fn(),
  fetchFilamentLevel: vi.fn(async () => ({
    ok: false,
    detail: "No printer address is configured.",
    percent: null,
  })),
}));

/** A datacentre build — the one a composed panel actually runs in. It can
 * slice and it can hand a file back; it has no route to a printer and nowhere
 * to record one. */
const HOSTED: PrintCapability = {
  slicer_available: true,
  slicer_path: "/s",
  slicer_hint: "",
  printer_configured: false,
  can_configure: false,
  mode: "download",
  can_send: false,
  can_download: true,
};

function version(kinds: string[]): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts: kinds.map((k) => ({
      kind: k as Version["artifacts"][number]["kind"], bytes: 1, part: 0,
    })),
  };
}

/** A panel of the shape a composed build ships: it reads what is open through
 * the contract, then renders the contract's own export UI around it.
 *
 * The `Version | null` annotation is load-bearing. It is the only thing in
 * this file that fails when `Version` stops being exported as a type — a
 * type-only export leaves nothing behind at runtime, so no assertion can
 * reach it and `tsc --noEmit` is what catches its loss. */
function ComposedPanel() {
  const active: Version | null = useActiveVersion();
  if (!active) return <p>Nothing is open.</p>;
  return <ExportShare version={active} />;
}

describe("the plugin contract", () => {
  it("composes a panel from the active version and the export UI", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
    const v = version(["step", "stl"]);

    renderWithProviders(<ComposedPanel />, { versions: [v], activeVersionId: v.id });

    // The formats come off the version the hook found, so seeing them proves
    // both halves met: a version that never arrived would render the fallback.
    expect(await screen.findByRole("button", { name: "STEP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "STL" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing is open.")).not.toBeInTheDocument();
  });

  it("says so when nothing is open, rather than rendering an empty export UI", () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);

    renderWithProviders(<ComposedPanel />);

    // `useActiveVersion` returning null is a normal state, not a failure, and a
    // panel has to be able to tell. Pinned because the hook's null branch is
    // the half a consumer gets wrong.
    expect(screen.getByText("Nothing is open.")).toBeInTheDocument();
  });
});

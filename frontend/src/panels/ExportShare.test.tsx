import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { PrintCapability, Version } from "../api";
import { ToastProvider } from "../components";
import { ExportShare } from "./ExportShare";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  fetchPrintCapability: vi.fn(),
  sliceVersion: vi.fn(),
  sendVersionToPrinter: vi.fn(),
}));

/** A deployment that can reach a printer: someone's own machine, with an
 * address saved. */
const LOCAL: PrintCapability = {
  slicer_available: true,
  slicer_path: "/s",
  slicer_hint: "",
  printer_configured: true,
  mode: "auto",
  can_send: true,
  can_download: true,
};

/** A deployment in a datacentre. It can slice; it has no route to the network
 * the printer is on, and no way for anyone to record one. */
const HOSTED: PrintCapability = {
  ...LOCAL,
  printer_configured: false,
  can_send: false,
};

function version(kinds: string[]): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts: kinds.map((k) => ({ kind: k as Version["artifacts"][number]["kind"], bytes: 1 })),
  };
}

function renderShare(v: Version) {
  return render(
    <ToastProvider>
      <ExportShare version={v} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ExportShare", () => {
  it("shows only the formats present on the version", () => {
    renderShare(version(["step", "glb"]));
    expect(screen.getByRole("button", { name: "STEP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GLB" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "STL" })).not.toBeInTheDocument();
  });

  it("downloads a format via fetch + blob and toasts success", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["x"]) });
    vi.stubGlobal("fetch", fetchFn);
    renderShare(version(["step", "stl", "obj", "glb"]));
    fireEvent.click(screen.getByRole("button", { name: "STL" }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(fetchFn.mock.calls[0][0]).toContain("/versions/5/artifacts/stl");
    await waitFor(() => expect(screen.getByText("STL downloaded")).toBeInTheDocument());
  });

  it("shows an error toast when an artifact is missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    renderShare(version(["obj"]));
    fireEvent.click(screen.getByRole("button", { name: "OBJ" }));
    await waitFor(() => expect(screen.getByText("Couldn't download OBJ")).toBeInTheDocument());
  });

  it("copies a share link", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderShare(version(["step"]));
    fireEvent.click(screen.getByRole("button", { name: "↗ Share" }));
    // Project id in the path, version pinned with ?v=.
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("/2?v=5")));
  });
});

describe("ExportShare printing", () => {
  afterEach(() => vi.clearAllMocks());

  const print = () => fireEvent.click(screen.getByRole("button", { name: /Print/ }));
  const sliced = (stats: Record<string, string> = {}) => ({
    ok: true, detail: "", slicer_missing: false, stats,
  });

  it("offers Print only when there is a mesh to slice", () => {
    renderShare(version(["step", "glb"]));
    expect(screen.queryByRole("button", { name: /Print/ })).not.toBeInTheDocument();
    renderShare(version(["step", "stl"]));
    expect(screen.getByRole("button", { name: /Print/ })).toBeInTheDocument();
  });

  it("says what to install when there is no slicer", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue({
      ...HOSTED,
      slicer_available: false,
      can_download: false,
      slicer_hint: "Install PrusaSlicer",
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByText("Install PrusaSlicer")).toBeInTheDocument());
    expect(api.sliceVersion).not.toHaveBeenCalled();
  });

  it("says so when an operator has switched printing off", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue({
      ...HOSTED, mode: "off", can_send: false, can_download: false,
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByText("Printing is off")).toBeInTheDocument());
    expect(api.sliceVersion).not.toHaveBeenCalled();
  });

  it("shows the cost and sends nothing until it is confirmed", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(
      sliced({ estimated_time: "1h 2m", filament_grams: "12.3" }),
    );
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByText(/1h 2m/)).toBeInTheDocument());
    expect(screen.getByText(/12\.3 g/)).toBeInTheDocument();
    expect(api.sendVersionToPrinter).not.toHaveBeenCalled();
  });

  it("sends only after the confirmation is accepted", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "10m" }));
    vi.mocked(api.sendVersionToPrinter).mockResolvedValue({
      ok: true, detail: "sent", reason: "", bytes_sent: 10,
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send to printer" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await waitFor(() => expect(api.sendVersionToPrinter).toHaveBeenCalledWith(5));
    await waitFor(() => expect(screen.getByText("Sent to the printer")).toBeInTheDocument());
  });

  it("sends nothing when the confirmation is dismissed", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.sendVersionToPrinter).not.toHaveBeenCalled();
  });

  it("sends the version it sliced, not whichever became active meanwhile", async () => {
    // The active version moves without a click: a chat or generation turn
    // finishing re-reads the project from an SSE event. This component is not
    // remounted when that happens, so reading the prop again at send time would
    // print a different model than the dialog described.
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "3h 12m" }));
    vi.mocked(api.sendVersionToPrinter).mockResolvedValue({
      ok: true, detail: "sent", reason: "", bytes_sent: 10,
    });

    const { rerender } = render(
      <ToastProvider>
        <ExportShare version={version(["stl"])} />
      </ToastProvider>,
    );
    print();
    await waitFor(() => expect(screen.getByText(/3h 12m/)).toBeInTheDocument());
    expect(api.sliceVersion).toHaveBeenCalledWith(5);

    rerender(
      <ToastProvider>
        <ExportShare version={{ ...version(["stl"]), id: 9 }} />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await waitFor(() => expect(api.sendVersionToPrinter).toHaveBeenCalled());
    expect(api.sendVersionToPrinter).toHaveBeenCalledWith(5);
    expect(api.sendVersionToPrinter).not.toHaveBeenCalledWith(9);
  });

  it("turns a slicer that vanished into guidance rather than an error toast", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue({
      ok: false, detail: "Install PrusaSlicer", slicer_missing: true, stats: {},
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByText("No slicer yet")).toBeInTheDocument());
  });

  it("reports a model the slicer refused, with the slicer's own words", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue({
      ok: false, detail: "Object too tall", slicer_missing: false, stats: {},
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByText("Object too tall")).toBeInTheDocument());
  });

  it("reports an unreachable printer after confirmation", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
    vi.mocked(api.sendVersionToPrinter).mockResolvedValue({
      ok: false, detail: "unreachable: no route", reason: "unreachable", bytes_sent: 0,
    });
    renderShare(version(["stl"]));
    print();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send to printer" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    await waitFor(() => expect(screen.getByText("Couldn't reach the printer")).toBeInTheDocument());
  });

  describe("on a deployment that cannot reach a printer", () => {
    it("still slices, rather than treating a missing address as a dead end", async () => {
      // The old behaviour refused here. On anything in a datacentre that is
      // every visitor, and the useful half of printing was reachable by nobody.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "45m" }));
      renderShare(version(["stl"]));
      print();
      await waitFor(() => expect(api.sliceVersion).toHaveBeenCalledWith(5));
      expect(screen.getByText(/45m/)).toBeInTheDocument();
    });

    it("offers the download and never the send", async () => {
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      renderShare(version(["stl"]));
      print();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument(),
      );
      expect(screen.queryByRole("button", { name: "Send to printer" })).not.toBeInTheDocument();
    });

    it("says why the file has to be carried over", async () => {
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      renderShare(version(["stl"]));
      print();
      await waitFor(() => expect(screen.getByText(/cannot reach a printer/)).toBeInTheDocument());
    });

    it("fetches the G-code with the action header and saves it", async () => {
      const fetchFn = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["G28"]) });
      vi.stubGlobal("fetch", fetchFn);
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      renderShare(version(["stl"]));
      print();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole("button", { name: "Download G-code" }));
      await waitFor(() => expect(fetchFn).toHaveBeenCalled());
      const [url, init] = fetchFn.mock.calls[0];
      expect(url).toContain("/printing/versions/5/gcode");
      // A plain <a href> cannot carry this, which is why the file is fetched.
      expect(init.headers).toMatchObject({ "X-Cadless-Action": "1" });
      await waitFor(() => expect(screen.getByText("G-code downloaded")).toBeInTheDocument());
    });
  });

  it("offers both actions where both can work", async () => {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
    vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
    renderShare(version(["stl"]));
    print();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send to printer" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument();
  });
});

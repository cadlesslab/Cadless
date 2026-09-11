import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { PrintCapability, Version } from "../api";
import { ToastProvider } from "../components";
import { ExportShare } from "./ExportShare";
import * as usb from "./usbPrinter";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  fetchPrintCapability: vi.fn(),
  sliceVersion: vi.fn(),
  sendVersionToPrinter: vi.fn(),
  // Mocked at the api layer like its neighbours, so it does not land in the
  // `fetch` stub the download tests count calls on. The default is the ordinary
  // answer for a deployment with no printer to ask.
  fetchFilamentLevel: vi.fn(async () => ({
    ok: false,
    detail: "No printer address is configured.",
    percent: null,
  })),
}));

/** The USB half is stubbed at the module boundary rather than below it.
 *
 * What this file is for is the panel: which actions it offers, what it says
 * before committing to one, and what it does with the answer. The conversation
 * with the printer has its own tests against a scripted fake port, and faking a
 * port here would only re-test that from further away.
 */
vi.mock("./usbPrinter", async (orig) => ({
  ...(await orig<typeof import("./usbPrinter")>()),
  isUsbPrintingSupported: vi.fn(() => false),
  requestPrinterPort: vi.fn(),
  handshake: vi.fn(),
  streamJob: vi.fn(),
}));

/** A deployment that can reach a printer: someone's own machine, with an
 * address saved. */
const LOCAL: PrintCapability = {
  slicer_available: true,
  slicer_path: "/s",
  slicer_hint: "",
  printer_configured: true,
  can_configure: true,
  mode: "auto",
  can_send: true,
  can_download: true,
};

/** The same machine before anyone has said where the printer is. There is
 * something for this reader to go and do. */
const FIRST_RUN: PrintCapability = {
  ...LOCAL,
  printer_configured: false,
  can_send: false,
};

/** A deployment in a datacentre. It can slice; it has no route to the network
 * the printer is on, and nowhere to record one either — which is what tells it
 * apart from FIRST_RUN, since the other fields are identical. */
const HOSTED: PrintCapability = {
  ...FIRST_RUN,
  can_configure: false,
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

/** A model split into `count` STL files, as one too big for the bed would be. */
function versionInPieces(count: number): Version {
  const v = version([]);
  return {
    ...v,
    artifacts: Array.from({ length: count }, (_, part) => ({ kind: "stl" as const, bytes: 1, part })),
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

  it("saves every piece of a model that comes in several", async () => {
    // One click, one model, every file of it. Saving only the first piece is
    // the silent half-delivery the part axis exists to prevent, and a reader
    // has no way to notice that two thirds of their model never arrived.
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["x"]) });
    vi.stubGlobal("fetch", fetchFn);
    renderShare(versionInPieces(3));
    fireEvent.click(screen.getByRole("button", { name: /STL/ }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(3));
    expect(fetchFn.mock.calls.map((c) => String(c[0]))).toEqual([
      expect.stringContaining("/versions/5/artifacts/stl/0"),
      expect.stringContaining("/versions/5/artifacts/stl/1"),
      expect.stringContaining("/versions/5/artifacts/stl/2"),
    ]);
  });

  it("says on the chip how many pieces a format is in", () => {
    renderShare(versionInPieces(3));
    expect(screen.getByRole("button", { name: "STL · 3 pieces" })).toBeInTheDocument();
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

    it("does not also promise the printer is about to start", async () => {
      // The two sentences were written for different deployments and were
      // being shown together, contradicting each other in one breath.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "45m" }));
      renderShare(version(["stl"]));
      print();
      await waitFor(() => expect(screen.getByText(/45m/)).toBeInTheDocument());
      expect(screen.queryByText(/will start as soon as it arrives/)).not.toBeInTheDocument();
    });
  });

  describe("on a machine that has not been told where the printer is", () => {
    it("points at Settings rather than claiming it cannot reach one", async () => {
      // Identical on the wire to the hosted case but for `can_configure`, and
      // the two readers need opposite things said to them: this one has
      // something to go and fix.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(FIRST_RUN);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      renderShare(version(["stl"]));
      print();
      // The address is in this panel now, so the instruction has to point at it
      // rather than at Settings, where it no longer is.
      await waitFor(() => expect(screen.getByText(/address below/)).toBeInTheDocument());
      expect(screen.queryByText(/cannot reach a printer/)).not.toBeInTheDocument();
    });

    it("still slices and offers the download", async () => {
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(FIRST_RUN);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      renderShare(version(["stl"]));
      print();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument(),
      );
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

  describe("a model that will not fit", () => {
    const offer = { percent: 12.5, size: [200, 112.5, 93.8] as [number, number, number] };
    const refusal = {
      ok: false,
      detail: "This model is 1600 x 900 x 750 mm, and the printer's build volume is 210 x 200 x 195 mm.",
      slicer_missing: false,
      stats: {},
      scale_offer: offer,
    };

    it("asks instead of stopping", async () => {
      // Most of the catalogue is furniture at real scale, so for that half the
      // plain refusal was always the last word.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(refusal);
      renderShare(version(["stl"]));
      print();

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Scale it and print" })).toBeInTheDocument(),
      );
      // Both sizes, and what it would become.
      expect(screen.getByText(/1600 x 900 x 750/)).toBeInTheDocument();
      expect(screen.getByText(/200 × 112.5 × 93.8 mm/)).toBeInTheDocument();
      expect(screen.getByText(/12.5%/)).toBeInTheDocument();
    });

    it("says what a scaled model is, before it is agreed to", async () => {
      // Not "the same thing, smaller". Only the reader knows which of those
      // they wanted, so the tool says what happens rather than choosing.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(refusal);
      renderShare(version(["stl"]));
      print();

      await screen.findByRole("button", { name: "Scale it and print" });
      expect(screen.getByText(/holes stop fitting what they were sized for/)).toBeInTheDocument();
    });

    it("slices nothing until the offer is accepted", async () => {
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(refusal);
      renderShare(version(["stl"]));
      print();

      await screen.findByRole("button", { name: "Cancel" });
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      // One call: the one that produced the refusal. Declining asks for nothing.
      expect(vi.mocked(api.sliceVersion).mock.calls).toHaveLength(1);
      expect(vi.mocked(api.sliceVersion).mock.calls[0][1]).toBeUndefined();
    });

    it("asks for a scaled job only once the answer is yes", async () => {
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion)
        .mockResolvedValueOnce(refusal)
        .mockResolvedValueOnce(sliced({ estimated_time: "10h 51m", filament_grams: "97.3" }));
      renderShare(version(["stl"]));
      print();

      await screen.findByRole("button", { name: "Scale it and print" });
      fireEvent.click(screen.getByRole("button", { name: "Scale it and print" }));

      await waitFor(() => expect(vi.mocked(api.sliceVersion).mock.calls).toHaveLength(2));
      // The version the dialog measured, not whichever is active by now: the
      // offer's numbers describe that one and no other.
      expect(vi.mocked(api.sliceVersion).mock.calls[1][0]).toBe(
        vi.mocked(api.sliceVersion).mock.calls[0][0],
      );
      expect(vi.mocked(api.sliceVersion).mock.calls[1][1]).toEqual({ scaleToFit: true });
      // And the numbers shown are the slicer's own, for the print that will run.
      await waitFor(() => expect(screen.getByText(/10h 51m/)).toBeInTheDocument());
    });

    it("says the model was scaled, on the screen that commits the material", async () => {
      // The figures are the slicer's own and describe the scaled object without
      // ever saying it is one, and the decision was made a dialog ago.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion)
        .mockResolvedValueOnce(refusal)
        .mockResolvedValueOnce({ ...sliced({ estimated_time: "10h 51m" }), scaled: true });
      renderShare(version(["stl"]));
      print();

      await screen.findByRole("button", { name: "Scale it and print" });
      fireEvent.click(screen.getByRole("button", { name: "Scale it and print" }));

      await waitFor(() =>
        expect(screen.getByText(/Scaled down to fit the bed/)).toBeInTheDocument(),
      );
    });

    it("meets the same answers on the second call as on the first", async () => {
      // The scaled call used to keep a shorter list of what can come back, so a
      // slicer that went missing between the two was reported as a model that
      // could not be prepared, with the install hint as the body.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion)
        .mockResolvedValueOnce(refusal)
        .mockResolvedValueOnce({
          ok: false,
          detail: "Install PrusaSlicer to print.",
          slicer_missing: true,
          stats: {},
        });
      renderShare(version(["stl"]));
      print();

      await screen.findByRole("button", { name: "Scale it and print" });
      fireEvent.click(screen.getByRole("button", { name: "Scale it and print" }));

      await waitFor(() => expect(screen.getByText("No slicer yet")).toBeInTheDocument());
    });

    it("still fails plainly when there is nothing to offer", async () => {
      // A model the slicer refused for some other reason has no question to ask.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue({
        ok: false, detail: "Object too tall", slicer_missing: false, stats: {},
      });
      renderShare(version(["stl"]));
      print();

      await waitFor(() => expect(screen.getByText("Object too tall")).toBeInTheDocument());
      expect(screen.queryByRole("button", { name: "Scale it and print" })).not.toBeInTheDocument();
    });

    it("passes on what the slicer said about a print it did make", async () => {
      // It matters most here, where the tool proposed the shape.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue({
        ...sliced({ estimated_time: "1h" }),
        warning: "print warning: Low bed adhesion. Consider enabling brim.",
      });
      renderShare(version(["stl"]));
      print();

      await waitFor(() => expect(screen.getByText(/Low bed adhesion/)).toBeInTheDocument());
    });
  });

  describe("what the machine has left", () => {
    it("shows it beside what the print will take", async () => {
      // The pair is the whole question somebody is answering at this dialog.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "1h", filament_grams: "12.3" }));
      vi.mocked(api.fetchFilamentLevel).mockResolvedValue({
        ok: true, detail: "", percent: 98, loaded: true, grams_left: 689.6,
      });
      renderShare(version(["stl"]));
      print();

      await waitFor(() => expect(screen.getByText(/12\.3 g/)).toBeInTheDocument());
      expect(screen.getByText(/About 690 g left/)).toBeInTheDocument();
    });

    it("asks the printer while the slicer is running, not before it", async () => {
      // Slicing is the slow step, so the round trip costs nothing on the clock.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced());
      vi.mocked(api.fetchFilamentLevel).mockResolvedValue({
        ok: true, detail: "", percent: 50, loaded: true,
      });
      renderShare(version(["stl"]));
      print();

      await waitFor(() => expect(api.fetchFilamentLevel).toHaveBeenCalled());
      expect(api.sliceVersion).toHaveBeenCalled();
    });

    it("offers the print anyway when the printer will not answer", async () => {
      // A printer that is off must not be able to stop a job being offered.
      vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL);
      vi.mocked(api.sliceVersion).mockResolvedValue(sliced({ estimated_time: "1h" }));
      vi.mocked(api.fetchFilamentLevel).mockRejectedValue(new Error("unreachable"));
      renderShare(version(["stl"]));
      print();

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Send to printer" })).toBeInTheDocument(),
      );
      expect(screen.queryByText(/cartridge/i)).not.toBeInTheDocument();
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

describe("printing over USB", () => {
  afterEach(() => vi.clearAllMocks());

  // The exact label, not /Print/: once the dialog is open two buttons match
  // that pattern, and a helper that silently picks one is a trap for later.
  const openPrint = () => fireEvent.click(screen.getByRole("button", { name: "⎙ Print" }));
  const usbButton = () => screen.getByRole("button", { name: "Print over USB" });
  const slice = (stats: Record<string, string> = {}) => ({
    ok: true, detail: "", slicer_missing: false, stats,
  });
  // Nothing reads it: every function that would is mocked in this file.
  const somePort = {} as usb.PrinterPort;

  async function openDialog(cap: PrintCapability) {
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(cap);
    vi.mocked(api.sliceVersion).mockResolvedValue(slice({ estimated_time: "45m" }));
    renderShare(version(["stl"]));
    openPrint();
    await waitFor(() => expect(screen.getByText(/45m/)).toBeInTheDocument());
  }

  /** A browser with Web Serial, a printer chosen, and a job that answers. */
  function withPrinter(baudRate = 115200) {
    vi.mocked(usb.isUsbPrintingSupported).mockReturnValue(true);
    vi.mocked(usb.requestPrinterPort).mockResolvedValue(somePort);
    vi.mocked(usb.handshake).mockResolvedValue({ ok: true, baudRate, firmware: "Marlin 2.1" });
  }

  it("offers nothing over USB where the browser has none", async () => {
    // Safari, and every browser before Firefox 151. The action is absent
    // rather than present-and-failing, and nothing else about the dialog moves.
    vi.mocked(usb.isUsbPrintingSupported).mockReturnValue(false);
    await openDialog(HOSTED);
    expect(screen.queryByRole("button", { name: "Print over USB" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument();
    // And the cost of a path they cannot take is not read at them: there it
    // would look like a rule about the download.
    expect(screen.queryByText(/tab must stay open/)).not.toBeInTheDocument();
  });

  it("offers it where the browser has it", async () => {
    withPrinter();
    await openDialog(HOSTED);
    expect(usbButton()).toBeInTheDocument();
  });

  it("states the tab must stay open before anything is committed", async () => {
    // Where the decision is made, not after it. Somebody is agreeing to keep a
    // tab open for the length of a print.
    withPrinter();
    await openDialog(HOSTED);
    expect(screen.getByText(/tab must stay open/)).toBeInTheDocument();
    expect(usb.requestPrinterPort).not.toHaveBeenCalled();
  });

  it("leaves the deployment's own two actions exactly as they were", async () => {
    // Web Serial is a fact about the browser. It must not move what the server
    // reports it can do, in either direction.
    withPrinter();
    await openDialog(LOCAL);
    expect(screen.getByRole("button", { name: "Send to printer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download G-code" })).toBeInTheDocument();
    expect(usbButton()).toBeInTheDocument();
  });

  it("asks for a port before the job leaves the server", async () => {
    // Two reasons in one order. Requesting the port spends the click's user
    // activation, so awaiting anything before it leaves the chooser with no
    // gesture to open on; and a dismissed chooser must not have cost a
    // download of a job nobody is going to print.
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    withPrinter();
    vi.mocked(usb.requestPrinterPort).mockRejectedValue(new Error("No port selected."));

    await openDialog(HOSTED);
    fireEvent.click(usbButton());

    await waitFor(() => expect(usb.requestPrinterPort).toHaveBeenCalled());
    expect(fetchFn).not.toHaveBeenCalled();
    expect(usb.streamJob).not.toHaveBeenCalled();
    // Dismissing the chooser is an answer rather than a fault, so the dialog
    // is still there to try again from.
    expect(usbButton()).toBeInTheDocument();
  });

  it("sends no G-code to something that is not a printer", async () => {
    // A serial port is just a port -- a debug console, a modem, an Arduino
    // running something else. This is the whole reason for the handshake.
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    withPrinter();
    vi.mocked(usb.handshake).mockResolvedValue({
      ok: false,
      detail: "Nothing on that port answered like a printer.",
    });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());

    await waitFor(() => expect(screen.getByText(/answered like a printer/)).toBeInTheDocument());
    expect(usb.streamJob).not.toHaveBeenCalled();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("streams the job it sliced, at the rate the handshake found", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, text: async () => "G28\nG1 X10\n" });
    vi.stubGlobal("fetch", fetchFn);
    withPrinter(250000);
    vi.mocked(usb.streamJob).mockResolvedValue({ ok: true, detail: "Sent 2 lines." });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());

    await waitFor(() => expect(usb.streamJob).toHaveBeenCalled());
    const [, gcode, options] = vi.mocked(usb.streamJob).mock.calls[0];
    // The same route and the same header the download uses. Same bytes, a
    // different destination -- no server change was needed for any of this.
    expect(fetchFn.mock.calls[0][0]).toContain("/printing/versions/5/gcode");
    expect(fetchFn.mock.calls[0][1].headers).toMatchObject({ "X-Cadless-Action": "1" });
    expect(gcode).toContain("G1 X10");
    // Not a guess and not a question put to the reader: the rate the printer
    // actually answered at.
    expect(options.baudRate).toBe(250000);
    await waitFor(() => expect(screen.getByText("Printed over USB")).toBeInTheDocument());
  });

  it("shows how far through it is", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => "G28\n" }));
    withPrinter();
    vi.mocked(usb.streamJob).mockImplementation(async (_port, _gcode, options) => {
      options.onProgress?.({ sent: 40, total: 100 });
      return { ok: true, detail: "done" };
    });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());

    await waitFor(() => expect(screen.getByText(/40 of 100 lines sent/)).toBeInTheDocument());
  });

  it("stops a running print when asked", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => "G28\n" }));
    withPrinter();
    let signal: AbortSignal | undefined;
    vi.mocked(usb.streamJob).mockImplementation(async (_port, _gcode, options) => {
      signal = options.signal;
      options.onProgress?.({ sent: 1, total: 100 });
      await new Promise<void>((resolve) => {
        options.signal?.addEventListener("abort", () => resolve());
      });
      return { ok: false, stopped: true, detail: "Stopped. The heaters were turned off." };
    });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());
    await waitFor(() => expect(screen.getByText(/1 of 100 lines sent/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    // The signal is what reaches the stream, and the stream is what turns the
    // heaters off -- a Stop that only closed the dialog would leave a hot
    // nozzle parked over the part.
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await waitFor(() => expect(screen.getByText("Print stopped")).toBeInTheDocument());
  });

  it("opens one chooser however many times the button is pressed", async () => {
    // The dialog deliberately stays open behind the chooser, so the button is
    // still there to be clicked again. Two choosers would race two handshakes
    // at the same port.
    withPrinter();
    let release: (port: usb.PrinterPort) => void = () => {};
    vi.mocked(usb.requestPrinterPort).mockReturnValue(
      new Promise<usb.PrinterPort>((resolve) => {
        release = resolve;
      }),
    );

    await openDialog(HOSTED);
    fireEvent.click(usbButton());
    await waitFor(() => expect(usb.requestPrinterPort).toHaveBeenCalledTimes(1));
    fireEvent.click(usbButton());
    fireEvent.click(usbButton());

    expect(usb.requestPrinterPort).toHaveBeenCalledTimes(1);
    release(somePort);
  });

  it("does not start a print that was cancelled while the chooser was up", async () => {
    // The chooser is an await, and the dialog behind it deliberately stays open
    // -- dismissing the chooser has to leave something to try again from. So
    // Cancel, Esc and a click outside are all still reachable while it is up,
    // and a print starting after somebody pressed Cancel is the one outcome
    // this dialog exists to prevent.
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    withPrinter();
    let release: (port: usb.PrinterPort) => void = () => {};
    vi.mocked(usb.requestPrinterPort).mockReturnValue(
      new Promise<usb.PrinterPort>((resolve) => {
        release = resolve;
      }),
    );

    await openDialog(HOSTED);
    fireEvent.click(usbButton());
    await waitFor(() => expect(usb.requestPrinterPort).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    release(somePort);
    // Long enough for the continuation after the chooser to run, if it were
    // going to.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(usb.handshake).not.toHaveBeenCalled();
    expect(usb.streamJob).not.toHaveBeenCalled();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("offers a way out while it is still looking for the printer", async () => {
    // Connecting walks up to four baud candidates and opens a port on each,
    // over a dialog that cannot be dismissed. A disabled Stop there is not a
    // slow exit, it is no exit -- the tab has to be reloaded.
    vi.stubGlobal("fetch", vi.fn());
    vi.mocked(usb.isUsbPrintingSupported).mockReturnValue(true);
    vi.mocked(usb.requestPrinterPort).mockResolvedValue(somePort);
    let signal: AbortSignal | undefined;
    vi.mocked(usb.handshake).mockImplementation(async (_port, _bauds, given) => {
      signal = given;
      await new Promise<void>((resolve) => {
        given?.addEventListener("abort", () => resolve());
      });
      return { ok: false, detail: "Stopped before a printer answered." };
    });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());
    await waitFor(() => expect(screen.getByText(/Looking for a printer/)).toBeInTheDocument());

    const stop = screen.getByRole("button", { name: "Stop" });
    expect(stop).not.toBeDisabled();
    fireEvent.click(stop);

    // The signal reaching `handshake` at all is the other half of this: without
    // it the abort has nothing to interrupt.
    await waitFor(() => expect(signal?.aborted).toBe(true));
  });

  it("reports a printer that stopped answering, rather than claiming success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => "G28\n" }));
    withPrinter();
    vi.mocked(usb.streamJob).mockResolvedValue({
      ok: false,
      detail: "The printer stopped answering.",
    });

    await openDialog(HOSTED);
    fireEvent.click(usbButton());

    await waitFor(() => expect(screen.getByText("The print stopped")).toBeInTheDocument());
    expect(screen.getByText("The printer stopped answering.")).toBeInTheDocument();
  });
});

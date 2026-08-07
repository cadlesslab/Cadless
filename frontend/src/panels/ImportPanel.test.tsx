import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { API_BASE } from "../config";
import { renderWithProviders } from "../test/utils";
import { ImportPanel } from "./ImportPanel";

const IMPORTED: api.ImportResult = {
  id: "l-bracket",
  name: "L Bracket",
  digest: "a".repeat(64),
  digest_confirmed: false,
  steps_checked: 3,
  project_id: 7,
};

function clsFile(name = "l-bracket.cls") {
  return new File([new Uint8Array([80, 75, 3, 4])], name, {
    type: "application/octet-stream",
  });
}

function choose(file: File) {
  fireEvent.change(screen.getByLabelText(/package file/i), { target: { files: [file] } });
}

describe("ImportPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("is offered without signing in", async () => {
    // A package handed over directly passed no upload check anywhere, and that
    // is the delivery with nothing else checking it. Putting the way to check
    // it behind an account would turn away the case it exists for.
    //
    // Asserted here by where the component is willing to reach: rendered on
    // its own it addresses nothing off this origin. Stated against the network
    // rather than against one function's name, which is why it kept holding
    // when the client it once shared a panel with left this repository — a spy
    // on a name that had gone would have stopped compiling rather than started
    // failing.
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchFn);

    renderWithProviders(<ImportPanel />);

    expect(await screen.findByLabelText(/package file/i)).toBeInTheDocument();

    // Driven far enough to reach the network before anything is claimed about
    // where it reaches. Asserting at the mount observed nothing at all — this
    // panel asks for nothing until a file is chosen — so the filter below ran
    // over an empty list and would have passed against any panel whatsoever.
    // Reaching the network is also what proves the spy is the thing the client
    // uses: one that was never connected reports the same empty list.
    choose(clsFile());
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());

    // Measured against this build's own API base, not against the document's
    // origin. In development the API is served from another port, so the client
    // legitimately addresses a different origin — asserting same-origin would
    // have been asserting something it never promised. What it does promise is
    // the docstring's "reaches no server but this one".
    const addressed = fetchFn.mock.calls.map(([url]) => String(url));
    expect(addressed.length).toBeGreaterThan(0);
    expect(addressed.filter((u) => !u.startsWith(`${API_BASE}/`))).toEqual([]);
  });

  it("will not import until a file has been chosen", async () => {
    const send = vi.spyOn(api, "importCatalog");

    renderWithProviders(<ImportPanel />);

    expect(await screen.findByRole("button", { name: /^Import/ })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it("imports the chosen file and reports what was checked before it was written", async () => {
    const send = vi.spyOn(api, "importCatalog").mockResolvedValue(IMPORTED);
    const file = clsFile();

    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(file);
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));

    await waitFor(() => expect(send).toHaveBeenCalledWith(file, undefined));
    expect(await screen.findByText(/Imported L Bracket/)).toBeInTheDocument();
    // The gate ran before anything reached disk, and saying how much it covered
    // is what separates "checked" from "checked nothing".
    expect(screen.getByText(/3 steps passed/)).toBeInTheDocument();
  });

  it("never lets a package handed over directly read as a verified one", async () => {
    // The only claim this panel must never make. Nothing was offered to check
    // this copy against, which is not the same as having found it unedited.
    vi.spyOn(api, "importCatalog").mockResolvedValue(IMPORTED);

    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(clsFile());
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));

    expect(await screen.findByText(/nothing to prove it is the package/i)).toBeInTheDocument();
    expect(screen.queryByText(/matches the fingerprint/i)).not.toBeInTheDocument();
  });

  it("keeps the fingerprint out of the way without taking it away", async () => {
    // Almost nobody has one, so it does not get a line of its own — but the
    // person who was sent one is exactly the person who should be able to use
    // it, and it is the only thing that notices an edit made after publishing.
    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);

    expect(screen.queryByLabelText("Fingerprint (optional)")).toBeNull();

    // Its own fold, not the explanation's. A field is something to fill in, and
    // a card that floats over the panel is no place to put one.
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));

    expect(screen.getByLabelText("Fingerprint (optional)")).toBeInTheDocument();
  });

  it("checks the package against a fingerprint the sender gave", async () => {
    const send = vi.spyOn(api, "importCatalog").mockResolvedValue({
      ...IMPORTED,
      digest_confirmed: true,
    });
    const file = clsFile();

    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(file);
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    fireEvent.change(screen.getByLabelText("Fingerprint (optional)"), {
      target: { value: "b".repeat(64) },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));

    await waitFor(() => expect(send).toHaveBeenCalledWith(file, "b".repeat(64)));
    expect(await screen.findByText(/matches the fingerprint/i)).toBeInTheDocument();
  });

  it("says why a package was refused instead of dropping it", async () => {
    vi.spyOn(api, "importCatalog").mockRejectedValue(
      new Error("steps/01.py: disallowed import: os"),
    );

    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(clsFile());
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));

    expect(await screen.findByText(/disallowed import: os/)).toBeInTheDocument();
  });

  it("says when the item was already here rather than claiming a fresh load", async () => {
    vi.spyOn(api, "importCatalog").mockResolvedValue({ ...IMPORTED, project_id: null });

    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(clsFile());
    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));

    expect(await screen.findByText(/already here/i)).toBeInTheDocument();
  });
});

describe("ImportPanel layout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("draws inside the app's panel chrome, like every other rail entry", async () => {
    // It used to be a section *inside* another panel, which is what
    // supplied the card around it — background, padding, and a heading in the
    // app's own hierarchy. Given a rail entry of its own, nothing did, and it
    // rendered as bare text over the viewport.
    //
    // Asserted structurally rather than by looking: the chrome is what `Panel`
    // emits, and the title being an `h2` is how it takes its place among the
    // other panels rather than reading as a heading inside one.
    const { container } = renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);

    expect(container.querySelector("section.panel")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Import a package" })).toBeInTheDocument();
  });

  it("brings its own styling rather than borrowing another panel's", async () => {
    // The panel this one was once a section of has left the repository and its
    // stylesheet went with it. A class named for that panel would have lost its
    // declarations at that moment, with nothing to compile, lint or test
    // against it — so every class this panel adds carries its own prefix.
    //
    // Each class is named here rather than counted. Counting was the first
    // attempt and it decided nothing: `import-advanced` is on the Advanced
    // button, present from the first render and never unmounted, so a length
    // check was satisfied at every later stage by that one class. Measured by
    // mutation — renaming all four of the conditionally mounted classes left
    // this file green. Naming them is what makes the walk mean anything, and
    // the walk is the point: three of them mount only once a file is chosen,
    // or the fold is opened, or an import has come back.
    vi.spyOn(api, "importCatalog").mockResolvedValue(IMPORTED);
    const { container } = renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    const own = () =>
      new Set(
        [...container.querySelectorAll("[class]")]
          .flatMap((el) => [...el.classList])
          .filter((c) => c.startsWith("import-")),
      );

    expect(own()).toContain("import-advanced");

    choose(clsFile());
    expect(own()).toContain("import-chosen");

    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByLabelText("Fingerprint (optional)")).toBeInTheDocument();
    expect(own()).toContain("import-field");
    expect(own()).toContain("import-head");

    fireEvent.click(screen.getByRole("button", { name: /^Import/ }));
    await screen.findByText(/Imported L Bracket/);
    expect(own()).toContain("import-result");
  });

  it("asks for a package file and nothing else", async () => {
    // One line: the explanation is a question mark away, and the fingerprint
    // field asked for something almost nobody has.
    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);

    expect(screen.queryByText(/went through no upload check/)).toBeNull();
  });

  it("explains itself when the question mark is used", async () => {
    renderWithProviders(<ImportPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "About importing a package" }));

    expect(screen.getByText(/went through no upload check/)).toBeInTheDocument();
  });

  it("says the explanation once", async () => {
    // It used to be written twice — once for the tooltip and once for the
    // paragraph underneath — so the two could drift apart while both claimed
    // to be the explanation.
    renderWithProviders(<ImportPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "About importing a package" }));

    expect(screen.getAllByText(/went through no upload check/)).toHaveLength(1);
  });

  it("names the file that was chosen instead of leaving it to the browser", async () => {
    // The control says "no file selected" until it does not, and then says the
    // name in a font nobody chose. Worth repeating in the panel's own voice,
    // because picking the wrong package is silent otherwise.
    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    choose(clsFile("gearbox.cls"));

    expect(screen.getByText(/gearbox\.cls/)).toBeInTheDocument();
  });

  it("counts one byte as one byte", async () => {
    renderWithProviders(<ImportPanel />);
    await screen.findByLabelText(/package file/i);
    fireEvent.change(screen.getByLabelText(/package file/i), {
      target: { files: [new File([new Uint8Array(1)], "tiny.cls")] },
    });

    expect(screen.getByText(/1 byte(?!s)/)).toBeInTheDocument();
  });
});

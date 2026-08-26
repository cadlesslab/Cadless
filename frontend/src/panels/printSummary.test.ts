import { describe, expect, it } from "vitest";

import { filamentNote, sliceSummary, USB_TETHER_WARNING } from "./printSummary";

describe("sliceSummary", () => {
  it("states both numbers when the slicer reported them", () => {
    const text = sliceSummary({ estimated_time: "1h 2m 3s", filament_grams: "12.34" });
    expect(text).toContain("1h 2m 3s");
    expect(text).toContain("12.34 g");
  });

  it("states only what is known", () => {
    const text = sliceSummary({ estimated_time: "45m" });
    expect(text).toContain("45m");
    expect(text).not.toContain("filament");
  });

  it("still says something when the slicer reported nothing", () => {
    // Reaching the dialog means slicing worked, so an empty message would read
    // as a failure rather than as a missing detail.
    expect(sliceSummary({})).toContain("ready");
    expect(sliceSummary(undefined)).toContain("ready");
  });

  it("says what the caller says happens next", () => {
    // What follows the numbers is not a property of the slice: one deployment
    // starts the print, another hands the file over. A fixed sentence made the
    // dialog say both.
    const text = sliceSummary({ estimated_time: "45m" }, "Take it to your printer.");
    expect(text).toContain("45m");
    expect(text).toContain("Take it to your printer.");
    expect(text).not.toContain("will start as soon as it arrives");
  });

  it("uses the caller's closing even when there are no numbers", () => {
    expect(sliceSummary({}, "Take it to your printer.")).toContain("Take it to your printer.");
  });

  it("never renders an absent value as text", () => {
    for (const stats of [{}, { estimated_time: "" }, { filament_grams: "" }]) {
      expect(sliceSummary(stats)).not.toContain("undefined");
      expect(sliceSummary(stats)).not.toContain("null");
    }
  });
});

describe("USB_TETHER_WARNING", () => {
  it("says the tab has to stay open, in words the reader can act on", () => {
    // The whole point of the sentence. Somebody is agreeing to keep a tab open
    // for the length of a print, and a warning that does not say so is not one.
    expect(USB_TETHER_WARNING).toMatch(/tab/i);
    expect(USB_TETHER_WARNING).toMatch(/open/i);
  });

  it("says why, and not only what", () => {
    // "Keep this tab open" on its own reads as an arbitrary rule. It is a
    // consequence of the browser being the thing sending the job.
    expect(USB_TETHER_WARNING).toMatch(/browser/i);
  });
});

describe("filamentNote", () => {
  const level = (over: Record<string, unknown> = {}) => ({
    ok: true, percent: 98, loaded: true, grams_left: null, ...over,
  });

  it("says nothing when there is no printer to ask", () => {
    // No address, printer off, download-only build. One extra fact on the way
    // into a dialog must never turn into a line about its own absence.
    expect(filamentNote(null)).toBe("");
    expect(filamentNote({ ok: false, percent: null })).toBe("");
  });

  it("says so when the machine has no cartridge", () => {
    // The printer keeps reporting the last cartridge's figure here — its own
    // page says as much — so the honest line is about the absence, not a number.
    expect(filamentNote(level({ loaded: false }))).toMatch(/no cartridge/i);
  });

  it("gives a proportion when nobody has said what a full one holds", () => {
    // The printer reports a percentage and never says of what. Without a
    // capacity that is the whole of what is known, so it is the whole of what
    // is claimed.
    expect(filamentNote(level())).toBe("The cartridge is 98% full.");
  });

  it("gives grams once a capacity makes them comparable", () => {
    expect(filamentNote(level({ grams_left: 689.6 }), "12.3")).toBe(
      "About 690 g left in the cartridge.",
    );
  });

  it("warns when the print needs more than is left", () => {
    // The whole reason this is shown before the OK rather than after it.
    const note = filamentNote(level({ percent: 2, grams_left: 14 }), "120.5");
    expect(note).toMatch(/less than this print needs/);
    expect(note).toContain("14 g");
  });

  it("does not warn when the figures cannot be compared", () => {
    // A slicer that reported no weight — an older build, or the density that
    // was missing until now — must not be read as "needs nothing".
    expect(filamentNote(level({ grams_left: 14 }), undefined)).toMatch(/About 14 g left/);
    expect(filamentNote(level({ grams_left: 14 }), "")).not.toMatch(/less than/);
  });

  it("says nothing when the machine will not give a figure", () => {
    expect(filamentNote(level({ percent: null }))).toBe("");
  });
});

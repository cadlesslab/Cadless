import { describe, expect, it } from "vitest";

import { sliceSummary, USB_TETHER_WARNING } from "./printSummary";

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

import { describe, expect, it } from "vitest";

import { sliceSummary } from "./printSummary";

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

  it("never renders an absent value as text", () => {
    for (const stats of [{}, { estimated_time: "" }, { filament_grams: "" }]) {
      expect(sliceSummary(stats)).not.toContain("undefined");
      expect(sliceSummary(stats)).not.toContain("null");
    }
  });
});

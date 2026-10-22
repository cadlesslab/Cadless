import { describe, expect, it } from "vitest";

import { measureText } from "./measures";

describe("measureText", () => {
  it("writes a part's size and volume in millimetres", () => {
    expect(measureText(71347.3, [80, 56, 39])).toEqual({
      unit: "mm",
      size: "80.0 × 56.0 × 39.0",
      volume: "71347.3 mm³",
    });
  });

  it("writes a house's in metres, from the millimetres it is recorded in", () => {
    expect(measureText(158.058e9, [12400, 8400, 6900])).toEqual({
      unit: "m",
      size: "12.4 × 8.4 × 6.9",
      volume: "158.1 m³",
    });
  });

  it("turns to metres at ten metres along the longest side, whichever side that is", () => {
    expect(measureText(null, [300, 10000, 200]).unit).toBe("m");
    expect(measureText(null, [300, 9999, 200]).unit).toBe("mm");
  });

  it("writes what is recorded and leaves out what is not", () => {
    expect(measureText(12.5, null)).toEqual({ unit: "mm", size: null, volume: "12.5 mm³" });
    expect(measureText(null, [1, 2, 3])).toEqual({ unit: "mm", size: "1.0 × 2.0 × 3.0", volume: null });
  });
});

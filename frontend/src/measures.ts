/** How the viewer writes a version's recorded size and volume.
 *
 * Both are recorded in millimetres. A model ten metres or more along its
 * longest side is written in metres and cubic metres instead: a house in
 * millimetres is a row of five-digit sizes and a twelve-digit volume. */

export type LengthUnit = "mm" | "m";

/** Where the size and the volume turn to metres: the longest side, in mm. */
const METRES_FROM_MM = 10_000;

const MM_PER: Record<LengthUnit, number> = { mm: 1, m: 1000 };

export interface MeasureText {
  /** The unit the size is written in; the volume is in its cube. */
  unit: LengthUnit;
  /** The three sides, without the unit, or null with none recorded. */
  size: string | null;
  /** The volume with its unit, or null with none recorded. */
  volume: string | null;
}

export function measureText(
  volume: number | null | undefined,
  bbox: readonly number[] | null | undefined,
): MeasureText {
  const unit: LengthUnit = bbox && Math.max(...bbox) >= METRES_FROM_MM ? "m" : "mm";
  const per = MM_PER[unit];
  return {
    unit,
    size: bbox ? bbox.map((side) => (side / per).toFixed(1)).join(" × ") : null,
    volume: volume == null ? null : `${(volume / per ** 3).toFixed(1)} ${unit}³`,
  };
}

/** Turning the slicer's own summary into the sentence shown before printing.
 *
 * Pure, so the wording is unit-tested rather than only rendered. The values are
 * the slicer's -- already formatted, already in its units -- so nothing here
 * recomputes them; the job is to say what is known and to be honest when
 * nothing is.
 */

/** What the slicer reports, as it reports it. Every key optional: an older
 * build writes fewer of these comments, and a summary that assumed all three
 * would print "undefined" at the reader. */
export interface SliceStats {
  estimated_time?: string;
  filament_grams?: string;
  filament_mm?: string;
}

/** The line shown in the confirmation dialog.
 *
 * The fallback is deliberately not silence. Reaching the dialog means slicing
 * succeeded, so the reader still has a decision to make, and an empty message
 * would read as something having gone wrong.
 */
export function sliceSummary(stats: SliceStats | Record<string, string> | undefined): string {
  const parts: string[] = [];
  const time = stats?.estimated_time;
  const grams = stats?.filament_grams;
  if (time) parts.push(`about ${time}`);
  if (grams) parts.push(`${grams} g of filament`);

  if (parts.length === 0) {
    return "The model is sliced and ready. The printer will start as soon as it arrives.";
  }
  return `This print takes ${parts.join(" and uses ")}. The printer will start as soon as it arrives.`;
}

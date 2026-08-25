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

/** What happens next, when nothing more specific is known. */
export const DEFAULT_CLOSING = "The printer will start as soon as it arrives.";

/** The line shown in the confirmation dialog.
 *
 * `closing` is a parameter because what happens after the numbers is not a
 * property of the slice. A deployment that will send the job says the printer
 * starts; one that hands the file back says the opposite, and a fixed sentence
 * made the dialog contradict itself in consecutive breaths.
 *
 * The fallback is deliberately not silence. Reaching the dialog means slicing
 * succeeded, so the reader still has a decision to make, and an empty message
 * would read as something having gone wrong.
 */
export function sliceSummary(
  stats: SliceStats | Record<string, string> | undefined,
  closing: string = DEFAULT_CLOSING,
): string {
  const parts: string[] = [];
  const time = stats?.estimated_time;
  const grams = stats?.filament_grams;
  if (time) parts.push(`about ${time}`);
  if (grams) parts.push(`${grams} g of filament`);

  if (parts.length === 0) {
    return `The model is sliced and ready. ${closing}`;
  }
  return `This print takes ${parts.join(" and uses ")}. ${closing}`;
}

/** What the reader is agreeing to when they print over USB.
 *
 * Streaming makes the browser the print host: the job leaves this tab one line
 * at a time for as long as the print takes, which for a real part is hours.
 * Exported rather than written inline so the wording is unit-tested, and stated
 * in the dialog rather than after the click, because it is the cost the reader
 * is weighing against the walk to the printer.
 */
export const USB_TETHER_WARNING =
  "Your browser sends the job line by line, so this tab must stay open until the print finishes.";

/** What an error says, for showing to someone.
 *
 * A rejected request arrives as an `Error` carrying the server's refusal, and
 * that sentence is the whole point of showing it — it names the file and the
 * reason. Anything else that gets thrown is rendered rather than swallowed, so
 * an unexpected shape still reaches the screen instead of becoming silence.
 */
export function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

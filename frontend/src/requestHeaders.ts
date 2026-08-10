/** What a composed build adds to every request this app makes.
 *
 * A registry rather than an argument, for the reason the panel registry is one:
 * the calls that would have to carry the argument are this app's own, spread
 * across `api.ts`, and a build that is not in this repository cannot reach them.
 * Registered, a header is something a module hands in at load, and the calls
 * neither know nor ask what it means.
 *
 * **This module is a mechanism and holds no policy.** It does not know what any
 * header is for, does not name one, and never inspects a value. What a header
 * means belongs to the build that contributes it — which is what lets the same
 * seam carry a credential in one composition and nothing at all in another.
 *
 * Two limits are worth knowing before building on it, because neither is
 * visible from here:
 *
 * - **It reaches `fetch` and nothing else.** `api.ts` opens its progress streams
 *   with `EventSource`, which carries no custom header in any browser — no
 *   option, no argument, no workaround. A contributed header therefore never
 *   reaches the streaming generate and refine routes. That is a property of the
 *   browser API rather than of this registry, and a build that needs a header on
 *   those routes needs a different transport, not a different registry.
 * - **A source runs on the request's own path**, so it must be cheap and must
 *   not await. It is asked once per request rather than once at registration:
 *   a value a build contributes is allowed to change between requests, and
 *   reading it once would send the first answer forever.
 */

/** Headers to add to this request, by name.
 *
 * Called per request. What it returns is merged in registration order, so a
 * later source wins a name it shares with an earlier one — and the caller's own
 * headers win over all of them, because a call that spelled a header out meant
 * that one.
 */
export type HeaderContributor = () => Record<string, string>;

const SOURCES: HeaderContributor[] = [];

/** Add `contribute` to what every request carries, and hand back its withdrawal.
 *
 * The withdrawal is returned rather than offered as a matching `unregister`
 * taking the same function: two calls with the same source are two sources, and
 * a name-based withdrawal could not tell them apart. Calling it twice is
 * harmless — the second call finds nothing to remove and takes nothing else
 * with it.
 */
export function registerRequestHeaders(contribute: HeaderContributor): () => void {
  SOURCES.push(contribute);
  let withdrawn = false;
  return () => {
    if (withdrawn) return;
    withdrawn = true;
    const at = SOURCES.indexOf(contribute);
    if (at !== -1) SOURCES.splice(at, 1);
  };
}

/** Every contributed header for the request being made now.
 *
 * Not exported to plugins: a build contributes headers and does not read what
 * the others contributed. `api.ts` is the only caller.
 *
 * A source that throws is left to throw. It is code this build was composed
 * with rather than input from outside it, so a throw is a defect in the build —
 * and a request that quietly went out without the header a source exists to add
 * is the worse of the two outcomes, because the server refuses it for a reason
 * nothing on this side names.
 */
export function contributedHeaders(): Record<string, string> {
  const contributed: Record<string, string> = {};
  for (const contribute of SOURCES) Object.assign(contributed, contribute());
  return contributed;
}

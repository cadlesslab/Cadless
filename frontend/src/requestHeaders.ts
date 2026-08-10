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
 * - **It reaches the `fetch` calls in `api.ts` and nothing else.** Not every
 *   request this app makes is one of those. `api.ts` opens its progress streams
 *   with `EventSource`, which carries no custom header in any browser — no
 *   option, no argument, no workaround — so a contributed header never reaches
 *   the streaming generate and refine routes. Nor does it reach the browser's
 *   own retrievals: the artifact download in `panels/ExportShare.tsx`, the model
 *   the viewport loads through `useGLTF` in `viewport/Viewport.tsx`, or a
 *   thumbnail an `<img>` asks for. A deployment that gated every route on a
 *   contributed header would break downloads and previews as well as the
 *   streams. Covering those needs a different transport, not a different
 *   registry.
 * - **A source runs on the request's own path**, so it must be cheap and must
 *   not await. It is asked once per request rather than once at registration:
 *   a value a build contributes is allowed to change between requests, and
 *   reading it once would send the first answer forever.
 */

import { setHeader } from "./headers";

/** Headers to add to this request, by name.
 *
 * Called per request. What it returns is merged in registration order, so a
 * later source wins a name it shares with an earlier one — and the caller's own
 * headers win over all of them, because a call that spelled a header out meant
 * that one.
 */
export type HeaderContributor = () => Record<string, string>;

/** One registration, boxed so a withdrawal can find its own.
 *
 * The box is the identity, not the function: the same function may be
 * registered twice, and those are two registrations. Searching for the function
 * would find whichever was registered first and remove that one — the count
 * comes out right, so nothing leaks, but the survivor moves to the end of the
 * list and takes a shared name from whoever was legitimately after it.
 */
interface Registration {
  contribute: HeaderContributor;
}

const SOURCES: Registration[] = [];

/** Add `contribute` to what every request carries, and hand back its withdrawal.
 *
 * The withdrawal is returned rather than offered as a matching `unregister`
 * taking the same function: two calls with the same source are two
 * registrations, and a lookup by value could not tell them apart. Calling it
 * twice is harmless — the second call returns without touching the list, so it
 * cannot take a later registration of the same function with it.
 */
export function registerRequestHeaders(contribute: HeaderContributor): () => void {
  const registration: Registration = { contribute };
  SOURCES.push(registration);
  let withdrawn = false;
  return () => {
    if (withdrawn) return;
    withdrawn = true;
    const at = SOURCES.indexOf(registration);
    if (at !== -1) SOURCES.splice(at, 1);
  };
}

/** Every contributed header for the request being made now, by lowercased name.
 *
 * Not exported to plugins: a build contributes headers and does not read what
 * the others contributed. `api.ts` is the only caller.
 *
 * Merged through `Headers` rather than into an object, because an object merges
 * by key and header names are case-insensitive. Two sources spelling one name
 * differently would both survive a key merge, and the two values would reach
 * the server comma-joined as a single header — which is not "the later source
 * wins" but "both do", and on `content-type` it is measurably a 422 rather than
 * a subtle preference. Names come back lowercased, which is what `fetch` sends
 * regardless of how they were spelled.
 *
 * A source that throws is left to throw. It is code this build was composed
 * with rather than input from outside it, so a throw is a defect in the build —
 * and a request that quietly went out without the header a source exists to add
 * is the worse of the two outcomes, because the server refuses it for a reason
 * nothing on this side names. Nothing is half-applied either way: a throw
 * leaves here before any merge and before `fetch`, so the request does not go.
 *
 * **A source holding a credential must keep it out of the message it throws.**
 * The throw travels through `req`, and `errMessage` renders `Error.message`
 * straight onto the screen.
 *
 * A value the *runtime* rejects is not left to say so itself — `setHeader`
 * refuses naming the header and never its value, for the reason recorded in
 * `headers.ts`.
 *
 * The list is copied before it is walked. A source that registers another
 * during the pass would otherwise be visited in the same pass — and one that
 * registers on every call would never finish, on the request's own path. The
 * copy cuts the other way too: a source withdrawn mid-pass is still asked, so a
 * revoked value goes out on the request already in flight.
 */
export function contributedHeaders(): Record<string, string> {
  const contributed = new Headers();
  for (const { contribute } of [...SOURCES]) {
    for (const [name, value] of Object.entries(contribute())) {
      setHeader(contributed, name, value);
    }
  }
  return Object.fromEntries(contributed.entries());
}

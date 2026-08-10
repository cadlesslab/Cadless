/** Writing a name and value into a `Headers`, without the runtime narrating the
 * value back to whoever is watching.
 *
 * `Headers` refuses a name that is not a token and a value with an interior CR,
 * LF or NUL, and on at least one engine the `TypeError` it raises **quotes what
 * it refused**. Every value passing through here may be a credential — a
 * composed build contributes one per request, and a plugin calling `request`
 * may spell its own — and the throw travels out through `errMessage`, which
 * renders `Error.message` straight onto the screen. So a value one newline away
 * from valid would be displayed in full, without anything on this side ever
 * deciding to display it.
 *
 * Its own module because two places write headers — the contributor merge and
 * the caller's own — and the first was guarded a round before the second was
 * noticed. One helper is what stops them drifting apart again.
 */

/** What a header name may be made of — RFC 9110's `token`, which is what
 * `Headers` itself accepts (measured: identical verdicts across U+0000-U+02FF).
 * Used only to decide whether a refused name is safe to repeat in an error,
 * never to vet one on the way in: `Headers` is the authority on that, and
 * answering it here as well would be two answers.
 */
const TOKEN = /^[A-Za-z0-9!#$%&'*+.^_`|~-]+$/;

function refuse(name: string): never {
  // The name is repeated only once it is known to be a bare token. A name
  // spelled badly enough to be refused is not a name worth putting on screen
  // either — a build that put something in the name would have it read aloud.
  throw new Error(
    TOKEN.test(name)
      ? `a request header is not valid: ${name}`
      : "a request header is not valid: its name is not a header name",
  );
}

/** `headers.set(name, value)`, refusing without quoting the value. */
export function setHeader(headers: Headers, name: string, value: string): void {
  try {
    headers.set(name, value);
  } catch {
    refuse(name);
  }
}

/** `headers.append(name, value)`, refusing without quoting the value. */
export function appendHeader(headers: Headers, name: string, value: string): void {
  try {
    headers.append(name, value);
  } catch {
    refuse(name);
  }
}

/** A caller's `HeadersInit` as name/value pairs, in the order it wrote them.
 *
 * Read here rather than handed to `new Headers(...)`, which is what puts the
 * values behind the guard above: that constructor validates them all at once,
 * so a bad one throws from inside the runtime with nothing on this side holding
 * it. A `Headers` is already past that check by construction, so it is the one
 * shape that can be read straight through.
 *
 * **Every shape the constructor took, this takes.** Reading it as the three
 * shapes `HeadersInit` names — a `Headers`, an array of pairs, a plain object —
 * is what the type says and not what the runtime does: the constructor takes
 * *any* iterable of pairs, so a `Map`, a `Set` of pairs, a generator or a
 * `URLSearchParams` all worked before. Narrowed to the three, each of those
 * falls to `Object.entries` and comes back empty — the caller's headers vanish
 * with nothing raised, and a caller overriding a contributed credential gets
 * the contributed one sent instead. A plugin is published JavaScript and the
 * type it declares is not a promise about what it passes.
 */
export function headerPairs(init: HeadersInit): [string, string][] {
  if (init instanceof Headers) {
    const pairs: [string, string][] = [];
    init.forEach((value, name) => pairs.push([name, value]));
    return pairs;
  }
  const iterable = (init as Partial<Iterable<[string, string]>>)?.[Symbol.iterator];
  if (typeof iterable === "function") {
    return [...(init as Iterable<[string, string]>)].map(([name, value]) => [name, value]);
  }
  return Object.entries(init);
}

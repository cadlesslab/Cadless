import { describe, expect, it } from "vitest";

import { appendHeader, headerPairs, setHeader } from "./headers";

/** A `Headers` that refuses everything, quoting what it refused.
 *
 * The runtime under these tests does not: jsdom's message is the constant
 * `value is invalid`, so asserting redaction against the real one asserts
 * nothing — a round of review shipped exactly that. The engines the code's
 * comment names *do* quote, so the property has to be measured against a
 * refusal that behaves like theirs.
 */
function quoting(): Headers {
  return {
    set(_name: string, value: string) {
      throw new TypeError(`Headers.set: "${value}" is an invalid header value.`);
    },
    append(_name: string, value: string) {
      throw new TypeError(`Headers.append: "${value}" is an invalid header value.`);
    },
  } as unknown as Headers;
}

const SECRET = "sk-live-SECRETVALUE";

describe("writing a header", () => {
  it("keeps a refused value out of the error, however the runtime words it", () => {
    expect(() => setHeader(quoting(), "X-Model-Key", SECRET)).toThrow(
      expect.objectContaining({ message: expect.not.stringContaining("SECRETVALUE") }),
    );
    expect(() => appendHeader(quoting(), "X-Model-Key", SECRET)).toThrow(
      expect.objectContaining({ message: expect.not.stringContaining("SECRETVALUE") }),
    );
  });

  it("names the header it refused, so the build can find it", () => {
    expect(() => setHeader(quoting(), "X-Model-Key", SECRET)).toThrow("X-Model-Key");
  });

  it("does not repeat a name that is not a header name", () => {
    // A name refused for its own spelling is not one worth echoing either: a
    // build that put something in the name would have it read aloud.
    expect(() => setHeader(quoting(), `X-Key: ${SECRET}`, "one")).toThrow(
      expect.objectContaining({ message: expect.not.stringContaining("SECRETVALUE") }),
    );
  });

  it("writes what the runtime accepts", () => {
    const headers = new Headers();

    setHeader(headers, "X-Example", "one");
    appendHeader(headers, "X-Example", "two");

    expect(headers.get("x-example")).toBe("one, two");
  });

  it("refuses what the real runtime refuses", () => {
    // The stub above proves the message; this proves the refusal is the
    // runtime's own rather than a second opinion written here.
    expect(() => setHeader(new Headers(), "X-Example", "a\r\nb")).toThrow("X-Example");
    expect(() => setHeader(new Headers(), "X Example", "one")).toThrow(
      "its name is not a header name",
    );
  });
});

describe("reading a caller's headers", () => {
  it("reads all three shapes in the order they were written", () => {
    expect(headerPairs({ "X-A": "1", "X-B": "2" })).toEqual([
      ["X-A", "1"],
      ["X-B", "2"],
    ]);
    expect(
      headerPairs([
        ["X-A", "1"],
        ["X-A", "2"],
      ]),
    ).toEqual([
      ["X-A", "1"],
      ["X-A", "2"],
    ]);
    expect(headerPairs(new Headers({ "X-A": "1" }))).toEqual([["x-a", "1"]]);
  });

  it("keeps the array's repeats rather than collapsing them", () => {
    // `new Headers` would have combined them, and combining is what the merge
    // downstream still does — but it has to happen behind the guard, so the
    // pairs arrive here uncombined.
    expect(
      headerPairs([
        ["X-A", "1"],
        ["X-A", "2"],
      ]),
    ).toHaveLength(2);
  });
});

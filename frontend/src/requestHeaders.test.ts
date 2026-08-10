import { afterEach, describe, expect, it } from "vitest";

import { contributedHeaders, registerRequestHeaders } from "./requestHeaders";

const withdrawals: (() => void)[] = [];

function register(contribute: () => Record<string, string>) {
  const withdraw = registerRequestHeaders(contribute);
  withdrawals.push(withdraw);
  return withdraw;
}

afterEach(() => {
  while (withdrawals.length) withdrawals.pop()?.();
});

describe("request header contributors", () => {
  it("contributes nothing when a build registered nothing", () => {
    expect(contributedHeaders()).toEqual({});
  });

  it("contributes what a registered source returns", () => {
    register(() => ({ "X-Example": "one" }));
    expect(contributedHeaders()).toEqual({ "x-example": "one" });
  });

  it("asks every time rather than once", () => {
    // The value a composed build contributes is allowed to change between
    // requests — a credential typed into a panel mid-session is the case this
    // exists for. Reading the sources once at registration would send the first
    // answer forever, and the request that proved it would be the one after the
    // visitor changed anything.
    let answer = "before";
    register(() => ({ "X-Example": answer }));

    expect(contributedHeaders()["x-example"]).toBe("before");
    answer = "after";
    expect(contributedHeaders()["x-example"]).toBe("after");
  });

  it("lets the last registered source win a name it shares", () => {
    // Registration order decides, on the same rule the panel registry follows:
    // the alternative — first one wins — makes the outcome depend on module
    // load order, which nobody controls.
    register(() => ({ "X-Example": "first" }));
    register(() => ({ "X-Example": "second" }));

    expect(contributedHeaders()["x-example"]).toBe("second");
  });

  it("withdraws a source through the function it handed back", () => {
    // A registry a test can only add to is one whose additions outlive the test
    // that made them.
    const withdraw = register(() => ({ "X-Example": "one" }));

    withdraw();

    expect(contributedHeaders()).toEqual({});
  });

  it("withdraws only itself when several are registered", () => {
    const withdrawFirst = register(() => ({ "X-First": "1" }));
    register(() => ({ "X-Second": "2" }));

    withdrawFirst();

    expect(contributedHeaders()).toEqual({ "x-second": "2" });
  });

  it("lets the last registered source win a name spelled another way", () => {
    // Merged into an object these are two keys, and both would reach the server
    // under one name with the values comma-joined — two credentials on one
    // request rather than the later one winning.
    register(() => ({ "X-Example": "first" }));
    register(() => ({ "x-example": "second" }));

    expect(Object.values(contributedHeaders())).toEqual(["second"]);
  });

  it("withdraws the registration it belongs to when one source is registered twice", () => {
    // The same function twice is two registrations. Removing whichever matches
    // by value takes the wrong one: the count still comes out right, so nothing
    // leaks — but the survivor moves to the end and takes the shared name from
    // whoever was legitimately after it. Registered first, third, with another
    // source between them, because withdrawing the *first* registration is the
    // one case a by-value lookup gets right by accident.
    const shared = () => ({ "X-Example": "from-shared" });
    register(shared);
    register(() => ({ "X-Example": "from-other" }));
    const withdrawThird = register(shared);

    withdrawThird();

    expect(contributedHeaders()["x-example"]).toBe("from-other");
  });

  it("survives being withdrawn twice", () => {
    const withdraw = register(() => ({ "X-Example": "one" }));
    withdraw();
    register(() => ({ "X-Other": "two" }));

    withdraw();

    expect(contributedHeaders()).toEqual({ "x-other": "two" });
  });

  it("does not visit a source that registered during the pass", () => {
    // A live array iterator rereads the length each step, so a source that
    // registers another while being asked would have it visited in the same
    // pass — and one that registers on every call would never finish, on the
    // request's own path rather than at load.
    let visits = 0;
    register(() => {
      visits += 1;
      if (visits < 5) register(() => ({ "X-Late": "late" }));
      return { "X-Example": "one" };
    });

    expect(contributedHeaders()).toEqual({ "x-example": "one" });
    expect(visits).toBe(1);
  });

  it("keeps a rejected value out of the error it raises", () => {
    // The runtime refuses a value with an interior newline, and on at least one
    // engine the TypeError quotes what it refused. `errMessage` renders that
    // straight into a toast, so a source holding a credential would have it on
    // screen without ever throwing on purpose.
    register(() => ({ "X-Model-Key": "sk-live-SECRETVALUE\r\nX-Injected: 1" }));

    expect(() => contributedHeaders()).toThrow("X-Model-Key");
    expect(() => contributedHeaders()).toThrow(
      expect.objectContaining({
        message: expect.not.stringContaining("SECRETVALUE"),
      }),
    );
  });

  it("does not repeat a name that is not a header name", () => {
    // A name refused for its own spelling is not a name worth echoing either —
    // a build that put something in the name would have it on screen.
    register(() => ({ "X-Model Key: sk-live-SECRETVALUE": "one" }));

    expect(() => contributedHeaders()).toThrow(
      expect.objectContaining({
        message: expect.not.stringContaining("SECRETVALUE"),
      }),
    );
  });

  it("lets a source that throws reach the caller", () => {
    // Not caught, and deliberately. A source is code this build was composed
    // with rather than input from outside it, so a throw is a defect in the
    // build — and a request that quietly went out without the header a source
    // exists to add is the worse outcome of the two: it would be refused by the
    // server for a reason nothing on this side names.
    register(() => {
      throw new Error("no key");
    });

    expect(() => contributedHeaders()).toThrow("no key");
  });
});

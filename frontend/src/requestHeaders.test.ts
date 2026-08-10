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
    expect(contributedHeaders()).toEqual({ "X-Example": "one" });
  });

  it("asks every time rather than once", () => {
    // The value a composed build contributes is allowed to change between
    // requests — a credential typed into a panel mid-session is the case this
    // exists for. Reading the sources once at registration would send the first
    // answer forever, and the request that proved it would be the one after the
    // visitor changed anything.
    let answer = "before";
    register(() => ({ "X-Example": answer }));

    expect(contributedHeaders()["X-Example"]).toBe("before");
    answer = "after";
    expect(contributedHeaders()["X-Example"]).toBe("after");
  });

  it("lets the last registered source win a name it shares", () => {
    // Registration order decides, on the same rule the panel registry follows:
    // the alternative — first one wins — makes the outcome depend on module
    // load order, which nobody controls.
    register(() => ({ "X-Example": "first" }));
    register(() => ({ "X-Example": "second" }));

    expect(contributedHeaders()["X-Example"]).toBe("second");
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

    expect(contributedHeaders()).toEqual({ "X-Second": "2" });
  });

  it("survives being withdrawn twice", () => {
    const withdraw = register(() => ({ "X-Example": "one" }));
    withdraw();
    register(() => ({ "X-Other": "two" }));

    withdraw();

    expect(contributedHeaders()).toEqual({ "X-Other": "two" });
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

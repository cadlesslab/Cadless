import { afterEach, describe, expect, it } from "vitest";

import "./app.css";

// vitest blanks CSS by default, so a suite that never loads this stylesheet
// cannot see which rule wins. `vite.config.ts` processes it for that reason;
// take it back out and every assertion below fails. Reading the file as text
// instead would catch a deleted rule and pass happily on one that is wrong.

/* The markup each path really produces, ancestors included. A bare element
   would let a rule scoped under `.chat-thread` or `.msg-body` walk straight
   past these assertions, and a scoped override is exactly how the views would
   end up back in a single column. */
const SETTLED = `
  <div class="chat-thread">
    <div class="msg-captures">
      <div class="msg msg-assistant msg-capture"><div class="msg-body">
        <img class="msg-image" alt=""></div></div>
      <div class="msg msg-assistant msg-capture"><div class="msg-body">
        <img class="msg-image" alt=""></div></div>
    </div>
  </div>`;

const LIVE = `
  <div class="chat-thread">
    <div class="msg msg-assistant"><div class="msg-body">
      <figure class="critique">
        <div class="critique-views">
          <img class="critique-view" alt=""><img class="critique-view" alt="">
        </div>
        <figcaption class="critique-verdict">Review of attempt 1</figcaption>
      </figure>
    </div></div>
  </div>`;

function mount(html: string): void {
  document.body.innerHTML = html;
}

function computed(selector: string): CSSStyleDeclaration {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`nothing matched ${selector}`);
  return getComputedStyle(el);
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the views of a critique round", () => {
  it("puts a settled round's views two to a row", () => {
    mount(SETTLED);
    const grid = computed(".msg-captures");
    expect(grid.display).toBe("grid");
    expect(grid.gridTemplateColumns).toMatch(/repeat\(2,/);
  });

  it("puts a live round's views two to a row", () => {
    mount(LIVE);
    const grid = computed(".critique-views");
    expect(grid.display).toBe("grid");
    expect(grid.gridTemplateColumns).toMatch(/repeat\(2,/);
  });

  it("gives a settled round's cells the shape a grid needs", () => {
    // Left as they are, each picture keeps the height cap and the indent it
    // carries as a row of its own, and the grid comes out ragged and indented
    // twice over.
    mount(SETTLED);
    expect(computed(".msg-captures .msg-capture").paddingLeft).toBe("0px");
    const cell = computed(".msg-captures .msg-image");
    expect(cell.width).toBe("100%");
    expect(cell.aspectRatio).toBe("1");
  });

  it("gives a live round's cells the shape a grid needs", () => {
    mount(LIVE);
    const cell = computed(".critique-view");
    expect(cell.width).toBe("100%");
    expect(cell.aspectRatio).toBe("1");
  });

  it("stops the pair of columns following the panel to its widest", () => {
    // Uncapped, a four-view round grows with the panel until it pushes the
    // reply that explains it off the top of the thread.
    // jsdom hands back the declared value rather than a resolved length, and
    // an empty string where there is no rule — so the token is what to look
    // for. `not.toBe("none")` would pass on that empty string.
    mount(SETTLED);
    expect(computed(".msg-captures").maxWidth).toContain("--views-max");
    mount(LIVE);
    expect(computed(".critique-views").maxWidth).toContain("--views-max");
  });
});

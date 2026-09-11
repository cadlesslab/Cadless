import { afterEach, describe, expect, it } from "vitest";

import "./app.css";

/** The rules that lay a critique round's views out, applied the way a browser
 * applies them.
 *
 * vitest blanks CSS by default, so a suite that never loads this stylesheet
 * cannot see which rule wins — which is how the anchoring defect the toast
 * tests document shipped green. `vite.config.ts` processes this file for the
 * same reason it processes `components.css`. Reading the text instead would
 * catch a deleted rule and pass happily on one that is present and wrong. */
function styled(className: string): HTMLElement {
  const el = document.createElement("div");
  el.className = className;
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the four views of a critique round", () => {
  // Both paths draw the same four pictures — the live round from one component,
  // the settled round from the container the thread groups them into — so both
  // have to lay them out the same way or a reload changes the shape.
  for (const className of ["critique-views", "msg-captures"]) {
    it(`.${className} puts the views two to a row`, () => {
      const style = getComputedStyle(styled(className));
      expect(style.display).toBe("grid");
      expect(style.gridTemplateColumns).toMatch(/repeat\(2,/);
    });
  }
});

import { describe, expect, it } from "vitest";

import { extractErrorLine, highlightLines, highlightPython } from "./codeHighlight";

describe("highlightPython", () => {
  it("wraps Python keywords/strings in token spans", () => {
    const html = highlightPython("from build123d import *");
    expect(html).toContain("token");
    expect(html).toContain("keyword");
  });

  it("splits code into one highlighted entry per line", () => {
    const lines = highlightLines("a = 1\nb = 2\n");
    expect(lines).toHaveLength(2); // trailing newline trimmed
  });
});

describe("extractErrorLine", () => {
  it("parses a line number from common error shapes", () => {
    expect(extractErrorLine("syntax error: unexpected EOF (line 3)")).toBe(3);
    expect(extractErrorLine('File "<generated>", line 12, in <module>')).toBe(12);
    expect(extractErrorLine("NameError: name 'x' is not defined")).toBeNull();
    expect(extractErrorLine(null)).toBeNull();
  });
});

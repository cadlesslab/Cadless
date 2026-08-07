import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./markdown";

describe("Markdown", () => {
  it("renders bold and italic spans", () => {
    const { container } = render(<Markdown text="a **bold** and *italic* word" />);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
  });

  it("renders inline code", () => {
    const { container } = render(<Markdown text="use `result` here" />);
    expect(container.querySelector("code")?.textContent).toBe("result");
  });

  it("renders bullet lists", () => {
    const { container } = render(<Markdown text={"items:\n- one\n- two"} />);
    const items = container.querySelectorAll("li");
    expect(items.length).toBe(2);
    expect(items[0].textContent).toBe("one");
    expect(items[1].textContent).toBe("two");
  });

  it("escapes HTML rather than injecting it", () => {
    const { container } = render(<Markdown text="<img src=x onerror=alert(1)>" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  // --- rich rendering -------------------------------------------

  it("renders headings at the right level", () => {
    const { container } = render(<Markdown text={"# Title\n## Sub"} />);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("h2")?.textContent).toBe("Sub");
  });

  it("renders a fenced code block as a code element", () => {
    const { container } = render(
      <Markdown text={"```python\nx = Box(1, 2, 3)\n```"} />,
    );
    const pre = container.querySelector("pre.md-code code");
    expect(pre?.textContent).toBe("x = Box(1, 2, 3)");
    // Fence markers are not shown literally.
    expect(container.textContent).not.toContain("```");
  });

  it("renders an ordered list", () => {
    const { container } = render(<Markdown text={"1. first\n2. second"} />);
    expect(container.querySelector("ol")).not.toBeNull();
    expect(container.querySelectorAll("ol > li").length).toBe(2);
  });

  it("renders a nested list", () => {
    const { container } = render(<Markdown text={"- a\n  - a1\n- b"} />);
    const topItems = container.querySelectorAll(":scope > div > ul > li");
    expect(topItems.length).toBe(2);
    expect(topItems[0].querySelector("ul li")?.textContent).toBe("a1");
  });

  it("renders a safe link and drops a javascript: link", () => {
    const ok = render(<Markdown text="see [docs](https://example.com)" />);
    const a = ok.container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("rel")).toContain("noopener");

    const bad = render(<Markdown text="[x](javascript:alert(1))" />);
    expect(bad.container.querySelector("a")).toBeNull(); // rendered as inert text
    expect(bad.container.textContent).toContain("[x](javascript:alert(1))");
  });

  it("renders a GFM pipe table", () => {
    const { container } = render(
      <Markdown text={"| A | B |\n| --- | --- |\n| 1 | 2 |"} />,
    );
    expect(container.querySelectorAll("table th").length).toBe(2);
    const cells = container.querySelectorAll("table tbody td");
    expect([cells[0].textContent, cells[1].textContent]).toEqual(["1", "2"]);
  });

  it("renders a blockquote", () => {
    const { container } = render(<Markdown text={"> quoted line"} />);
    expect(container.querySelector("blockquote")?.textContent).toBe("quoted line");
  });

  it("is streaming-safe with an unterminated code fence", () => {
    const { container } = render(<Markdown text={"```\nhalf streamed"} />);
    expect(container.querySelector("pre.md-code code")?.textContent).toBe("half streamed");
  });
});

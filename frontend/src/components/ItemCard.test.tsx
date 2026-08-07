import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ItemCard } from "./ItemCard";

function card(props: Partial<Parameters<typeof ItemCard>[0]> = {}) {
  return (
    <ItemCard
      title="L-Bracket"
      meta="bracket · someone"
      fallbackIcon={<i data-testid="fallback" />}
      onOpen={() => {}}
      {...props}
    />
  );
}

describe("ItemCard", () => {
  it("shows the picture it was given", () => {
    render(card({ thumbnailUrl: "/a.png" }));

    expect(document.querySelector(".item-thumb img")?.getAttribute("src")).toBe("/a.png");
  });

  it("falls back to the icon where there is no picture", () => {
    render(card());

    expect(document.querySelector(".item-thumb img")).toBeNull();
    expect(screen.getByTestId("fallback")).toBeInTheDocument();
  });

  it("falls back to the icon when a picture fails to arrive", () => {
    render(card({ thumbnailUrl: "/a.png" }));

    fireEvent.error(document.querySelector(".item-thumb img") as HTMLImageElement);

    expect(document.querySelector(".item-thumb img")).toBeNull();
    expect(screen.getByTestId("fallback")).toBeInTheDocument();
  });

  it("tries again when it is given a different picture", () => {
    // A grid reuses a card as its contents change — a new page, a re-search, a
    // listing that published a new version. Remembering only "something failed"
    // would leave the placeholder on every picture after the first bad one.
    const { rerender } = render(card({ thumbnailUrl: "/a.png" }));
    fireEvent.error(document.querySelector(".item-thumb img") as HTMLImageElement);

    rerender(card({ thumbnailUrl: "/b.png" }));

    expect(document.querySelector(".item-thumb img")?.getAttribute("src")).toBe("/b.png");
  });

  it("opens on the title, which is what makes the card reachable by name", () => {
    const onOpen = vi.fn();
    render(card({ onOpen }));

    fireEvent.click(screen.getByRole("button", { name: "L-Bracket" }));

    expect(onOpen).toHaveBeenCalledOnce();
  });
});

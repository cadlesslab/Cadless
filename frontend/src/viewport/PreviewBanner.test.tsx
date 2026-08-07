import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PreviewBanner } from "./PreviewBanner";
import { viewportStore } from "./viewportStore";

// Unmounted first: clearing while the banner is still mounted updates it from
// outside act(), which React reports as a warning on every run.
afterEach(() => {
  cleanup();
  act(() => viewportStore.clearPreview());
});

describe("PreviewBanner", () => {
  it("names what is being looked at and says it is not here", () => {
    viewportStore.showPreview({ url: "/depot/artifacts/c/v/a", title: "Angle bracket" });
    render(<PreviewBanner />);
    expect(screen.getByText("Angle bracket")).toBeInTheDocument();
    expect(screen.getByText(/not on this machine/i)).toBeInTheDocument();
  });

  it("stays out of the way when nothing is being previewed", () => {
    const { container } = render(<PreviewBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("gives a way back to your own work", () => {
    viewportStore.showPreview({ url: "/depot/artifacts/c/v/a", title: "Angle bracket" });
    render(<PreviewBanner />);
    fireEvent.click(screen.getByRole("button", { name: /stop previewing/i }));
    expect(viewportStore.get().preview).toBeNull();
  });

  it("says it even for a catalogue with nothing to draw", () => {
    viewportStore.showPreview({ url: null, title: "Gearbox", note: "no mesh" });
    render(<PreviewBanner />);
    expect(screen.getByText("Gearbox")).toBeInTheDocument();
  });
});

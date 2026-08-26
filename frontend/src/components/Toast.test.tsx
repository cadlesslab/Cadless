import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "./Toast";

function Triggers() {
  const toast = useToast();
  return (
    <>
      <button onClick={() => toast.success("Saved")}>ok</button>
      <button onClick={() => toast.error("Action failed", "boom")}>fail</button>
      <button onClick={() => toast.error("Second failure")}>fail-2</button>
      <button onClick={() => toast.toast({ title: "Raw error", variant: "error" })}>raw</button>
    </>
  );
}

describe("Toast", () => {
  it("hands out a stable api so an effect depending on it does not re-run", () => {
    // A consumer that lists useToast() in a useEffect dependency array must not
    // see a new identity every render — pushing a toast re-renders the provider,
    // so an unstable value re-runs the effect, which pushes again, and so on.
    const seen = new Set<unknown>();
    function Probe() {
      const toast = useToast();
      seen.add(toast);
      return <button onClick={() => toast.error("Action failed", "boom")}>fail</button>;
    }
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fail"));
    fireEvent.click(screen.getByText("fail"));
    expect(seen.size).toBe(1);
  });

  it("shows a toast pushed via useToast", () => {
    render(
      <ToastProvider>
        <Triggers />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fail"));
    expect(screen.getByText("Action failed")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("dismisses a toast on demand instead of only on a timer", () => {
    // Waiting it out and swiping right were the only ways to close a toast, and
    // the swipe has nothing on screen to advertise it.
    render(
      <ToastProvider>
        <Triggers />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fail"));
    expect(screen.getByText("Action failed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Action failed" }));
    expect(screen.queryByText("Action failed")).toBeNull();
  });

  it("dismisses only the toast whose button was clicked", () => {
    render(
      <ToastProvider>
        <Triggers />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fail"));
    fireEvent.click(screen.getByText("fail-2"));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Action failed" }));
    expect(screen.queryByText("Action failed")).toBeNull();
    expect(screen.getByText("Second failure")).toBeInTheDocument();
  });

  it("holds an error on screen until dismissed while ordinary toasts still expire", () => {
    // An error that vanishes mid-read cannot be recovered — there is no history.
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Triggers />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByText("ok"));
      fireEvent.click(screen.getByText("fail"));
      expect(screen.getByText("Saved")).toBeInTheDocument();
      expect(screen.getByText("Action failed")).toBeInTheDocument();

      act(() => void vi.advanceTimersByTime(10_000));

      expect(screen.queryByText("Saved")).toBeNull();
      expect(screen.getByText("Action failed")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("holds any error toast, not only the ones pushed through error()", () => {
    // Persistence belongs to the severity, not to which helper was called —
    // otherwise the generic API quietly hands back the four-second behaviour.
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Triggers />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByText("raw"));
      act(() => void vi.advanceTimersByTime(10_000));
      expect(screen.getByText("Raw error")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the error stack bounded instead of letting it grow over the app", () => {
    // Errors no longer expire and arrive one per failed action, so an unbounded
    // stack would creep back down over the controls this viewport was moved to
    // clear. Opening a panel whose load fails is enough to add one every time.
    render(
      <ToastProvider>
        <Triggers />
      </ToastProvider>,
    );
    const fail = screen.getByText("fail");
    for (let i = 0; i < 6; i++) fireEvent.click(fail);
    expect(screen.getAllByText("Action failed")).toHaveLength(4);
  });
});

describe("where a toast appears", () => {
  /** jsdom measures everything as nothing, which is exactly the "no usable
   * rect" case the placement refuses — so a test about placement has to give
   * the element a size before it can be about anything. */
  function sized(el: Element, rect: Partial<DOMRect>) {
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      top: 100, left: 40, right: 140, bottom: 130, width: 100, height: 30,
      x: 40, y: 100, toJSON: () => ({}), ...rect,
    } as DOMRect);
  }

  /** The card carrying this message. Placement lives on the message now, not on
   * the viewport they share — which is the whole of what this block is about. */
  const card = (text: string) =>
    [...document.querySelectorAll(".toast")].find((el) =>
      el.textContent?.includes(text),
    ) as HTMLElement;

  function mount() {
    render(
      <ToastProvider>
        <Triggers />
      </ToastProvider>,
    );
  }

  it("puts it beside the control that caused it", () => {
    // The whole point: on a wide monitor the far corner is far enough from the
    // button to be missed, and a message about something you just did belongs
    // where you just did it.
    mount();
    const button = screen.getByText("ok");
    sized(button, {});
    fireEvent.click(button);

    const saved = card("Saved");
    expect(saved.className).toContain("toast-anchored");
    expect(saved.style.top).toBe("100px");
    // To the right of the control, with room to breathe.
    expect(saved.style.left).toBe("148px");
  });

  it("does not move a message that is already up when the next one arrives", () => {
    // The failure this shape exists to prevent. Errors never time out, so two
    // cards from two controls is the ordinary state — and a place shared
    // between them meant the second dragged the first to a control it had
    // nothing to do with, which is the inverse of what anchoring is for.
    mount();
    const failing = screen.getByText("fail");
    sized(failing, { top: 100, left: 40, right: 140 });
    fireEvent.click(failing);
    const first = card("Action failed");
    expect(first.style.top).toBe("100px");

    const saving = screen.getByText("ok");
    sized(saving, { top: 400, left: 40, right: 140 });
    fireEvent.click(saving);

    // Each stays with the control that caused it.
    expect(card("Action failed").style.top).toBe("100px");
    expect(card("Saved").style.top).toBe("400px");
  });

  it("keeps the corner for a message with nothing to point at", () => {
    // An SSE event, a print ending an hour later. This is the case that stops
    // the feature being "anchor everything": a message with nowhere to anchor
    // must not be a message that disappears.
    function Spontaneous() {
      const toast = useToast();
      return <span ref={() => toast.success("Generation finished")} />;
    }
    render(
      <ToastProvider>
        <Spontaneous />
      </ToastProvider>,
    );
    const spontaneous = card("Generation finished");
    expect(spontaneous).toBeInTheDocument();
    expect(spontaneous.className).not.toContain("toast-anchored");
    expect(spontaneous.style.top).toBe("");
  });

  it("keeps the corner once the moment has passed", () => {
    // Slicing and printing take minutes to hours. By then the reader has looked
    // away, and a bubble beside a button nobody is watching says less than a
    // message where messages go.
    mount();
    const button = screen.getByText("ok");
    sized(button, {});
    fireEvent.click(button);
    expect(card("Saved").style.top).toBe("100px");

    const later = Date.now() + 10_000;
    try {
      vi.spyOn(Date, "now").mockReturnValue(later);
      fireEvent.click(screen.getByText("fail"));
      expect(card("Action failed").className).not.toContain("toast-anchored");
    } finally {
      vi.mocked(Date.now).mockRestore();
    }
    // ...and the one that was already placed did not move to join it.
    expect(card("Saved").style.top).toBe("100px");
  });

  it("keeps the corner when the control has gone", async () => {
    // The shape of a real one: a dialog closes on the click, and the toast
    // arrives when the request it started comes back — by which time the button
    // is not there to point at.
    function Vanishing() {
      const toast = useToast();
      const [gone, setGone] = useState(false);
      if (gone) return null;
      return (
        <button
          onClick={() => {
            setGone(true);
            void Promise.resolve().then(() => toast.success("Saved"));
          }}
        >
          go
        </button>
      );
    }
    render(
      <ToastProvider>
        <Vanishing />
      </ToastProvider>,
    );
    const button = screen.getByText("go");
    sized(button, {});
    fireEvent.click(button);

    await screen.findByText("Saved");
    expect(screen.queryByText("go")).toBeNull();
    expect(card("Saved").className).not.toContain("toast-anchored");
  });

  it("stays inside the window when there is no room beside the control", () => {
    // A control near the right edge would otherwise push the card half off
    // screen, which says less than the corner it came from.
    mount();
    const button = screen.getByText("ok");
    sized(button, { left: 900, right: 1000, top: 50 });
    fireEvent.click(button);

    const left = Number.parseInt(card("Saved").style.left, 10);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + 360).toBeLessThanOrEqual(window.innerWidth);
  });

  it("sends everything back to the corner when the window is resized", () => {
    // A placement is a set of window coordinates, and an error does not time
    // out — so a card placed against the old width would sit off the edge for
    // as long as it stayed open. The corner is the answer that is never wrong.
    mount();
    const button = screen.getByText("fail");
    sized(button, {});
    fireEvent.click(button);
    expect(card("Action failed").className).toContain("toast-anchored");

    fireEvent(window, new Event("resize"));

    expect(card("Action failed").className).not.toContain("toast-anchored");
  });
});

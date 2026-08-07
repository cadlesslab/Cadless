import { act, fireEvent, render, screen } from "@testing-library/react";
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

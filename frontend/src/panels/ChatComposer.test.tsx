import { fireEvent, render } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components";
import { ChatComposer } from "./ChatComposer";

function renderComposer(props: Partial<Parameters<typeof ChatComposer>[0]> = {}) {
  const ref = createRef<HTMLTextAreaElement>();
  const merged = {
    inputRef: ref,
    value: "",
    onChange: () => {},
    onSubmit: () => {},
    onStop: () => {},
    onQueue: () => {},
    generating: false,
    disabled: false,
    ...props,
  };
  return render(
    <ToastProvider>
      <ChatComposer {...merged} />
    </ToastProvider>,
  );
}

describe("ChatComposer Stop", () => {
  it("shows a Stop button while generating and calls onStop when clicked", () => {
    const onStop = vi.fn();
    const { getByLabelText } = renderComposer({ generating: true, onStop });
    const stop = getByLabelText("Stop");
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("shows the send button (not Stop) when idle", () => {
    const { queryByLabelText } = renderComposer({ generating: false });
    expect(queryByLabelText("Stop")).toBeNull();
    expect(queryByLabelText("Send")).not.toBeNull();
  });

  it("pulses the Stop button with a heartbeat while generating", () => {
    const { getByLabelText } = renderComposer({ generating: true });
    expect(getByLabelText("Stop").className).toContain("heartbeat");
  });
});

describe("ChatComposer queue/steer", () => {
  it("offers a Queue affordance distinct from Stop while generating", () => {
    const { getByLabelText, queryByLabelText } = renderComposer({
      generating: true,
      value: "make it red",
    });
    // Both the Stop control and a separate Queue control are present mid-stream.
    expect(getByLabelText("Stop")).not.toBeNull();
    expect(getByLabelText("Queue message")).not.toBeNull();
    // They are different elements (steering is not the same affordance as Stop).
    expect(getByLabelText("Queue message")).not.toBe(queryByLabelText("Stop"));
  });

  it("lets the user type while a turn is streaming", () => {
    const { getByPlaceholderText } = renderComposer({ generating: true });
    const input = getByPlaceholderText(/queue a message/i) as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
  });

  it("calls onQueue (not onSubmit) when queuing mid-stream", () => {
    const onQueue = vi.fn();
    const onSubmit = vi.fn();
    const { getByLabelText } = renderComposer({
      generating: true,
      value: "make it red",
      onQueue,
      onSubmit,
    });
    fireEvent.click(getByLabelText("Queue message"));
    expect(onQueue).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("queues on Enter while generating", () => {
    const onQueue = vi.fn();
    const { getByPlaceholderText } = renderComposer({
      generating: true,
      value: "steer",
      onQueue,
    });
    const input = getByPlaceholderText(/queue a message/i);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onQueue).toHaveBeenCalledTimes(1);
  });
});

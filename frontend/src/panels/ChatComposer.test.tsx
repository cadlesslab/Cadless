import { fireEvent, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components";
import { ChatComposer, type ComposerAttachment } from "./ChatComposer";

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

/** A file of a declared size. The bytes are token: `size` is a getter on `Blob`,
 * so shadowing it states a multi-megabyte case without allocating one, and the
 * limit checks read that number rather than the contents. */
function imageFile(name: string, type = "image/png", size = 4): File {
  const file = new File([new Uint8Array(4)], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function attached(name: string, bytes = 4): ComposerAttachment {
  return { media_type: "image/png", data: "AAAA", name, bytes };
}

/** Paste one or more files into the field, answering false when the composer
 * took the paste for itself. `fireEvent` defines `clipboardData` on the event,
 * because jsdom has no `DataTransfer` to build one from. */
function paste(input: HTMLElement, files: File[]): boolean {
  return fireEvent.paste(input, { clipboardData: { files } });
}

function pick(container: HTMLElement, files: File[]) {
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
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

describe("ChatComposer attachments", () => {
  it("keeps both pictures when a second paste lands before the first has finished reading", async () => {
    // Reading a file is async and the parent's state has not come back down yet,
    // so merging onto the captured prop makes the second write drop the first —
    // an attachment silently lost, which is the failure this feature is about.
    const onAttachmentsChange = vi.fn();
    const { getByPlaceholderText } = renderComposer({ onAttachmentsChange });
    const field = getByPlaceholderText(/Describe or refine/);

    paste(field, [imageFile("first.png")]);
    paste(field, [imageFile("second.png")]);

    await waitFor(() => expect(onAttachmentsChange).toHaveBeenCalledTimes(2));
    const last = onAttachmentsChange.mock.calls[1][0] as ComposerAttachment[];
    expect(last.map((a) => a.name)).toEqual(["first.png", "second.png"]);
  });

  it("takes a pasted image as an attachment", async () => {
    const onAttachmentsChange = vi.fn();
    const { getByPlaceholderText } = renderComposer({ onAttachmentsChange });

    const reachedField = paste(getByPlaceholderText(/Describe or refine/), [
      imageFile("bracket.png"),
    ]);

    // Taken by the composer, so the field never sees it.
    expect(reachedField).toBe(false);
    await waitFor(() => expect(onAttachmentsChange).toHaveBeenCalledTimes(1));
    expect(onAttachmentsChange.mock.calls[0][0]).toEqual([
      { media_type: "image/png", data: expect.any(String), name: "bracket.png", bytes: 4 },
    ]);
  });

  it("leaves a paste that carries no image to the field", () => {
    // Pasting text has to keep working: swallowing every paste would break the
    // ordinary case to serve the rare one.
    const onAttachmentsChange = vi.fn();
    const { getByPlaceholderText } = renderComposer({ onAttachmentsChange });

    const reachedField = paste(getByPlaceholderText(/Describe or refine/), [
      new File(["notes"], "notes.txt", { type: "text/plain" }),
    ]);

    expect(reachedField).toBe(true);
    expect(onAttachmentsChange).not.toHaveBeenCalled();
  });

  it("takes an image chosen from the file picker", async () => {
    const onAttachmentsChange = vi.fn();
    const { container } = renderComposer({ onAttachmentsChange });

    pick(container, [imageFile("sketch.jpg", "image/jpeg")]);

    await waitFor(() => expect(onAttachmentsChange).toHaveBeenCalledTimes(1));
    expect(onAttachmentsChange.mock.calls[0][0][0]).toMatchObject({
      media_type: "image/jpeg",
      name: "sketch.jpg",
    });
  });

  it("sends the base64 payload without the data: URL it was read as", async () => {
    // The server decodes what it is handed rather than parsing a URL out of it,
    // so a leading `data:image/png;base64,` is not a cosmetic difference.
    const onAttachmentsChange = vi.fn();
    const { container } = renderComposer({ onAttachmentsChange });

    pick(container, [imageFile("bracket.png")]);

    await waitFor(() => expect(onAttachmentsChange).toHaveBeenCalled());
    expect(onAttachmentsChange.mock.calls[0][0][0].data).not.toContain("data:");
    expect(onAttachmentsChange.mock.calls[0][0][0].data).not.toContain(",");
  });

  it("refuses an image over the per-image limit, naming the limit", async () => {
    const onAttachmentsChange = vi.fn();
    const { container, findByRole } = renderComposer({ onAttachmentsChange });

    pick(container, [imageFile("huge.png", "image/png", 3_750_001)]);

    expect(await findByRole("alert")).toHaveTextContent(/huge\.png.*3\.75 MB/);
    expect(onAttachmentsChange).not.toHaveBeenCalled();
  });

  it("refuses one image too many, naming the count limit", async () => {
    const onAttachmentsChange = vi.fn();
    const { container, findByRole } = renderComposer({
      attachments: [attached("a.png"), attached("b.png"), attached("c.png"), attached("d.png")],
      onAttachmentsChange,
    });

    pick(container, [imageFile("e.png")]);

    expect(await findByRole("alert")).toHaveTextContent(/at most 4 images/);
    expect(onAttachmentsChange).not.toHaveBeenCalled();
  });

  it("refuses attachments that together pass the per-turn limit", async () => {
    // Each one is under the per-image limit; it is the total that is refused, and
    // the total is of the decoded bytes rather than of the base64 they became.
    const onAttachmentsChange = vi.fn();
    const { container, findByRole } = renderComposer({
      attachments: [attached("a.png", 3_700_000), attached("b.png", 3_700_000)],
      onAttachmentsChange,
    });

    pick(container, [imageFile("c.png", "image/png", 200_000)]);

    expect(await findByRole("alert")).toHaveTextContent(/more than 7\.5 MB/);
    expect(onAttachmentsChange).not.toHaveBeenCalled();
  });

  it("refuses a format nothing downstream can encode, naming what it takes", async () => {
    const onAttachmentsChange = vi.fn();
    const { container, findByRole } = renderComposer({ onAttachmentsChange });

    pick(container, [imageFile("plan.svg", "image/svg+xml")]);

    expect(await findByRole("alert")).toHaveTextContent(
      /plan\.svg is not an image this can send — use png, jpeg, gif, webp\./,
    );
    expect(onAttachmentsChange).not.toHaveBeenCalled();
  });

  it("shows each attachment as a chip that can be taken back off", () => {
    const onAttachmentsChange = vi.fn();
    const { getByLabelText, getByText } = renderComposer({
      attachments: [attached("first.png"), attached("second.png")],
      onAttachmentsChange,
    });

    expect(getByText("first.png")).toBeInTheDocument();
    expect(getByText("second.png")).toBeInTheDocument();

    fireEvent.click(getByLabelText("Remove first.png"));

    expect(onAttachmentsChange).toHaveBeenCalledWith([attached("second.png")]);
  });

  it("enables Send with an attachment and no text at all", () => {
    const { getByLabelText } = renderComposer({
      value: "",
      attachments: [attached("bracket.png")],
      onAttachmentsChange: () => {},
    });
    expect(getByLabelText("Send")).toBeEnabled();
  });

  it("keeps Send disabled with neither text nor attachment", () => {
    const { getByLabelText } = renderComposer({ value: "", onAttachmentsChange: () => {} });
    expect(getByLabelText("Send")).toBeDisabled();
  });

  it("keeps Queue disabled on an attachment alone, since steering takes words", () => {
    // A steer message is injected into a running loop as a bare string; there is
    // nowhere in it for a picture to go.
    const { getByLabelText } = renderComposer({
      generating: true,
      value: "",
      attachments: [attached("bracket.png")],
      onAttachmentsChange: () => {},
    });
    expect(getByLabelText("Queue message")).toBeDisabled();
  });

  it("offers no attach control where the panel does not accept attachments", () => {
    const { container, queryByLabelText } = renderComposer();
    expect(queryByLabelText("Attach image")).toBeNull();
    expect(container.querySelector("input[type=file]")).toBeNull();
  });

  it("takes the attach control away on a read-only project", () => {
    const { getByLabelText } = renderComposer({ disabled: true, onAttachmentsChange: () => {} });
    expect(getByLabelText("Attach image")).toBeDisabled();
  });
});

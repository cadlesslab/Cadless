import { describe, expect, it, vi } from "vitest";

import {
  BAUD_CANDIDATES,
  COOL_DOWN,
  checksum,
  framed,
  handshake,
  isUsbPrintingSupported,
  type PrinterPort,
  printableLines,
  streamJob,
} from "./usbPrinter";

/** A printer on the other end of a port, driven by a script.
 *
 * The whole reason port acquisition is separate from the protocol: this is a
 * `PrinterPort` and never touches a browser, so the conversation can be tested
 * without a device — which is the half worth testing.
 */
function fakePrinter(reply: (line: string) => string[] | null) {
  const received: string[] = [];
  const opened: number[] = [];
  let closes = 0;
  let push: ((chunk: Uint8Array) => void) | null = null;
  let finish: (() => void) | null = null;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const port: PrinterPort = {
    async open({ baudRate }) {
      opened.push(baudRate);
    },
    async close() {
      closes += 1;
      finish?.();
    },
    readable: new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(chunk);
        finish = () => {
          try {
            controller.close();
          } catch {
            // already closed
          }
        };
      },
    }),
    writable: new WritableStream<Uint8Array>({
      write(chunk) {
        for (const line of decoder.decode(chunk).split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          received.push(trimmed);
          const answers = reply(trimmed);
          if (answers) for (const a of answers) push?.(encoder.encode(`${a}\n`));
        }
      },
    }),
  };

  return { port, received, opened, closes: () => closes };
}

/** The command inside a framed line, with its numbering and checksum removed. */
function commandOf(framedLine: string): string {
  return framedLine.replace(/^N\d+\s/, "").replace(/\*\d+$/, "");
}

describe("what the browser can do at all", () => {
  it("says no when the browser has no serial support", () => {
    // jsdom has none, which is the case a Safari visitor is in.
    expect(isUsbPrintingSupported()).toBe(false);
  });

  it("says yes once it does", () => {
    vi.stubGlobal("navigator", { serial: { requestPort: vi.fn() } });
    expect(isUsbPrintingSupported()).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("framing", () => {
  it("checksums by xor over everything before the star", () => {
    // Worked by hand rather than by calling the function under test.
    let expected = 0;
    for (const ch of "N1 G28") expected ^= ch.charCodeAt(0);
    expect(checksum("N1 G28")).toBe(expected);
  });

  it("numbers and checksums a command the way a host is expected to", () => {
    expect(framed(7, "G1 X10")).toMatch(/^N7 G1 X10\*\d+$/);
  });

  it("gives a different checksum to a different line number", () => {
    // Which is the point: the number is inside what the checksum covers, so a
    // line that arrives claiming the wrong number does not verify.
    expect(framed(1, "G28")).not.toBe(framed(2, "G28"));
  });

  it("drops comments and blank lines", () => {
    const lines = printableLines("G28 ; home\n\n; a whole comment\nG1 X1\n   \n");
    expect(lines).toEqual(["G28", "G1 X1"]);
  });

  it("keeps a line whose comment is the only thing removed", () => {
    expect(printableLines("M104 S200 ; heat")).toEqual(["M104 S200"]);
  });
});

describe("finding out whether it is a printer", () => {
  it("accepts a firmware that names itself", async () => {
    const { port, received } = fakePrinter((line) =>
      line.includes("M115") ? ["FIRMWARE_NAME:Marlin 2.1.2 SOURCE_CODE_URL:...", "ok"] : ["ok"],
    );
    const result = await handshake(port, [115200]);
    expect(result.ok).toBe(true);
    expect(result.baudRate).toBe(115200);
    expect(result.firmware).toContain("Marlin");
    expect(received).toContain("M115");
  });

  it("refuses a port that answers with nothing recognisable", async () => {
    // A debug console, a modem, an Arduino running something else. Streaming a
    // print into one of those is what the handshake exists to prevent.
    const { port } = fakePrinter(() => ["hello from some other device"]);
    const result = await handshake(port, [115200]);
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/answered like a printer/);
  });

  it("tries the next rate when the first stays silent", async () => {
    const { port, opened } = fakePrinter((line) => (line.includes("M115") ? null : null));
    await handshake(port, [115200, 250000]);
    expect(opened).toEqual([115200, 250000]);
  });

  it("closes the port on the way out of every attempt", async () => {
    const { port, closes } = fakePrinter(() => null);
    await handshake(port, [115200, 250000]);
    expect(closes()).toBe(2);
  });

  it("offers the rates a consumer printer actually uses", () => {
    expect(BAUD_CANDIDATES).toContain(115200);
    expect(BAUD_CANDIDATES).toContain(250000);
  });
});

describe("streaming a job", () => {
  const ackEverything = (line: string) => (line.startsWith("N") ? ["ok"] : []);

  it("sends every printable line, numbered, and reports success", async () => {
    const { port, received } = fakePrinter(ackEverything);
    const result = await streamJob(port, "G28\nG1 X10\n; a comment\n", { baudRate: 115200 });

    expect(result.ok).toBe(true);
    const commands = received.map(commandOf);
    expect(commands).toEqual(["M110 N0", "G28", "G1 X10"]);
  });

  it("waits for each ok before sending the next line, and Stop ends the wait", async () => {
    // Two things at once. A printer that never answers must not be streamed at
    // -- the whole file would go into a buffer that cannot hold it -- and the
    // reader must not be held there. Before the signal reached the wait, this
    // took ACK_TIMEOUT_MS to give up on a 50ms abort.
    const { port, received } = fakePrinter((line) =>
      commandOf(line) === "G28" ? [] : ["ok"],
    );
    const result = await streamJob(port, "G28\nG1 X10\n", {
      baudRate: 115200,
      signal: AbortSignal.timeout(50),
    });
    expect(result.ok).toBe(false);
    // `stopped`, not merely `!ok`: a wait the signal ended and a printer that
    // died both leave the job unfinished, and only one of them is the reader's
    // doing. Without this the test passes on the failure it was written to
    // catch, because a timed-out wait reports `!ok` too.
    expect(result.stopped).toBe(true);
    expect(received.map(commandOf)).not.toContain("G1 X10");
    // Stopping is what turns the heaters off, so it has to have reached the
    // part of the stream that does it.
    for (const command of COOL_DOWN) expect(received).toContain(command);
  });

  it("does not count busy as an acknowledgement", async () => {
    // A printer that is heating, homing or clearing its buffer answers `busy:`
    // and then `ok` once it is ready. Treating the first as the acknowledgement
    // would push the next line at a printer that just said it was not ready.
    let asked = 0;
    const { port, received } = fakePrinter((line) => {
      if (commandOf(line) !== "G28") return ["ok"];
      asked += 1;
      return ["busy: processing", "ok"];
    });
    const result = await streamJob(port, "G28\n", { baudRate: 115200 });
    expect(result.ok).toBe(true);
    expect(asked).toBe(1); // it kept listening rather than re-sending
    expect(received.map(commandOf).filter((c) => c === "G28")).toHaveLength(1);
  });

  it("resends from the line the printer asks for", async () => {
    // The reason the lines are numbered and checksummed at all: a mis-heard
    // line is recoverable instead of silently printed.
    let demanded = false;
    const { port, received } = fakePrinter((line) => {
      if (commandOf(line) === "G1 X10" && !demanded) {
        demanded = true;
        return ["Resend: 1"];
      }
      return ["ok"];
    });
    const result = await streamJob(port, "G28\nG1 X10\n", { baudRate: 115200 });

    expect(result.ok).toBe(true);
    // G28 went twice: once first time, once when it was asked for again.
    expect(received.map(commandOf).filter((c) => c === "G28")).toHaveLength(2);
  });

  it("gives up when the printer reports an error", async () => {
    const { port } = fakePrinter((line) =>
      commandOf(line) === "G1 X10" ? ["Error:Printer halted"] : ["ok"],
    );
    const result = await streamJob(port, "G28\nG1 X10\n", { baudRate: 115200 });
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/refused the job/);
  });

  it("reports progress as lines are acknowledged", async () => {
    const seen: number[] = [];
    const { port } = fakePrinter(ackEverything);
    await streamJob(port, "G28\nG1 X10\nG1 X20\n", {
      baudRate: 115200,
      onProgress: ({ sent, total }) => {
        seen.push(sent);
        expect(total).toBe(3);
      },
    });
    expect(seen).toEqual([1, 2, 3]);
  });

  it("refuses a job with nothing in it", async () => {
    const { port } = fakePrinter(ackEverything);
    const result = await streamJob(port, "; only comments\n\n", { baudRate: 115200 });
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/nothing to print/);
  });

  describe("stopping", () => {
    it("stops sending when asked", async () => {
      const controller = new AbortController();
      const { port, received } = fakePrinter((line) => {
        if (commandOf(line) === "G28") controller.abort();
        return ["ok"];
      });
      const result = await streamJob(port, "G28\nG1 X10\nG1 X20\n", {
        baudRate: 115200,
        signal: controller.signal,
      });

      expect(result.stopped).toBe(true);
      expect(received.map(commandOf)).not.toContain("G1 X20");
    });

    it("turns the heaters off on the way out", async () => {
      // Abandoning a print leaves a hot nozzle parked over the part. Three
      // commands is a cheap price for not doing that.
      const controller = new AbortController();
      const { port, received } = fakePrinter((line) => {
        if (commandOf(line) === "G28") controller.abort();
        return ["ok"];
      });
      await streamJob(port, "G28\nG1 X10\n", {
        baudRate: 115200,
        signal: controller.signal,
      });
      for (const command of COOL_DOWN) expect(received).toContain(command);
    });

    it("turns them off after a failure too, not only after a stop", async () => {
      const { port, received } = fakePrinter((line) =>
        commandOf(line) === "G28" ? ["Error:Thermal runaway"] : ["ok"],
      );
      await streamJob(port, "G28\n", { baudRate: 115200 });
      for (const command of COOL_DOWN) expect(received).toContain(command);
    });

    it("leaves them alone when the job finished", async () => {
      // A finished job has already run whatever end G-code the slicer wrote.
      const { port, received } = fakePrinter(ackEverything);
      await streamJob(port, "G28\n", { baudRate: 115200 });
      expect(received).not.toContain("M104 S0");
    });
  });
});

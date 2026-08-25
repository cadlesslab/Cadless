import { describe, expect, it, vi } from "vitest";

import {
  BAUD_CANDIDATES,
  COOL_DOWN,
  checksum,
  framed,
  HANDSHAKE_TIMEOUT_MS,
  handshake,
  isUsbPrintingSupported,
  MAX_CONSECUTIVE_RESENDS,
  type PrinterPort,
  printableLines,
  streamJob,
} from "./usbPrinter";

/** A printer on the other end of a port, driven by a script.
 *
 * The whole reason port acquisition is separate from the protocol: this is a
 * `PrinterPort` and never touches a browser, so the conversation can be tested
 * without a device — which is the half worth testing.
 *
 * `reply` is called with each line the host sends and returns the lines the
 * printer sends back, or `null` to say nothing at all. `refuseWrite` makes the
 * port reject a write, which is what a cable pulled mid-print looks like.
 */
function fakePrinter(
  reply: (line: string) => string[] | null,
  options: { refuseWrite?: (line: string) => boolean } = {},
) {
  const received: string[] = [];
  const opened: number[] = [];
  let closes = 0;
  let push: ((chunk: Uint8Array) => void) | null = null;
  let finish: (() => void) | null = null;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  let readable: ReadableStream<Uint8Array> | null = null;
  let writable: WritableStream<Uint8Array> | null = null;

  /** A real `SerialPort` hands out fresh streams on every `open()`.
   *
   * Building them once and reusing them made the second baud candidate read
   * from an already-closed stream and answer instantly, so no test ever
   * exercised a later candidate's actual wait — and the seam where `streamJob`
   * re-opens a port `handshake` closed was never crossed at all.
   */
  function makeStreams() {
    readable = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => {
          try {
            controller.enqueue(chunk);
          } catch {
            // The stream is closed; there is nobody left to tell.
          }
        };
        finish = () => {
          try {
            controller.close();
          } catch {
            // Already closed.
          }
        };
      },
    });
    writable = new WritableStream<Uint8Array>({
      write(chunk) {
        for (const line of decoder.decode(chunk).split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (options.refuseWrite?.(trimmed)) throw new Error("the port has gone");
          received.push(trimmed);
          const answers = reply(trimmed);
          if (answers) for (const a of answers) push?.(encoder.encode(`${a}\n`));
        }
      },
    });
  }

  const port: PrinterPort = {
    async open({ baudRate }) {
      opened.push(baudRate);
      makeStreams();
    },
    async close() {
      closes += 1;
      finish?.();
      readable = null;
      writable = null;
    },
    get readable() {
      return readable;
    },
    get writable() {
      return writable;
    },
  };

  return { port, received, opened, closes: () => closes };
}

/** A port that opens and then reports its stream finished at once — a device
 * that has gone, rather than one that is merely quiet.
 *
 * It answers in microseconds, which is what lets the walk over the baud
 * candidates be tested without spending the real budget on every case. One test
 * below does spend it, on purpose. */
function departedPort() {
  const opened: number[] = [];
  let closes = 0;
  const port: PrinterPort = {
    async open({ baudRate }) {
      opened.push(baudRate);
    },
    async close() {
      closes += 1;
    },
    readable: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.close();
      },
    }),
    writable: new WritableStream<Uint8Array>({ write() {} }),
  };
  return { port, opened, closes: () => closes };
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

  it("tries the next rate when the first one leads nowhere", async () => {
    const { port, opened } = departedPort();
    await handshake(port, [115200, 250000]);
    expect(opened).toEqual([115200, 250000]);
  });

  it("closes the port on the way out of every attempt", async () => {
    const { port, closes } = departedPort();
    await handshake(port, [115200, 250000]);
    expect(closes()).toBe(2);
  });

  it(
    "spends its whole budget on a quiet port before moving on",
    async () => {
      // The real worst case, measured rather than assumed. A port that is open
      // and silent — the wrong baud rate — costs HANDSHAKE_TIMEOUT_MS apiece,
      // and four candidates of that is what somebody watching the dialog sits
      // through. It is why Stop is enabled while this runs.
      const { port, opened } = fakePrinter(() => null);
      const started = Date.now();
      await handshake(port, [115200, 250000]);
      const elapsed = Date.now() - started;

      expect(opened).toEqual([115200, 250000]);
      // A loose bound: the point is that both waits really happened, not the
      // timer's precision.
      expect(elapsed).toBeGreaterThan(HANDSHAKE_TIMEOUT_MS);
    },
    20_000,
  );

  it(
    "stops walking the candidates when the caller stops it",
    async () => {
      // Without the signal this sat through every remaining candidate with Stop
      // pressed and a dialog that cannot be dismissed.
      const { port, opened } = fakePrinter(() => null);
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 50);

      const result = await handshake(port, [115200, 250000, 57600, 230400], controller.signal);

      expect(result.ok).toBe(false);
      expect(opened.length).toBeLessThan(4);
    },
    20_000,
  );

  it("reports a port that will not open, rather than throwing", async () => {
    const port: PrinterPort = {
      async open() {
        throw new Error("device busy");
      },
      async close() {},
      readable: null,
      writable: null,
    };
    const result = await handshake(port, [115200]);
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/would not open/);
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
    // — the whole file would go into a buffer that cannot hold it — and the
    // reader must not be held there. Before the signal reached the wait, this
    // took ACK_TIMEOUT_MS to give up on a 50ms abort.
    const { port, received } = fakePrinter((line) => (commandOf(line) === "G28" ? [] : ["ok"]));
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

  it("recovers from the three lines Marlin actually sends", async () => {
    // Marlin's `gcode_line_error` opens with `SERIAL_ERROR_START()`, so a
    // mis-heard line arrives as an Error, then the Resend, then an ok. Reading
    // that first line as fatal meant the checksum-and-resend machinery this
    // module is built around never once ran against real firmware: the first
    // corrupted byte ended the print instead of being recovered from.
    let demanded = false;
    const { port, received } = fakePrinter((line) => {
      if (commandOf(line) === "G1 X10" && !demanded) {
        demanded = true;
        return ["Error:checksum mismatch, Last Line: 1", "Resend: 1", "ok"];
      }
      return ["ok"];
    });
    const result = await streamJob(port, "G28\nG1 X10\n", { baudRate: 115200 });

    expect(result.ok).toBe(true);
    // G28 went twice: once first time, once when it was asked for again.
    expect(received.map(commandOf).filter((c) => c === "G28")).toHaveLength(2);
  });

  it("does not let the ok behind a resend acknowledge the re-sent line", async () => {
    // Marlin sends `ok` after the `Resend:` to keep the host's accounting
    // straight. Read as the acknowledgement of the line that is about to go out
    // again, it would put every ack after it one line out for the rest of the
    // job — so the last line would never be sent and the job would still report
    // success.
    let demanded = false;
    const { port, received } = fakePrinter((line) => {
      if (commandOf(line) === "G1 X10" && !demanded) {
        demanded = true;
        return ["Resend: 1", "ok"];
      }
      return ["ok"];
    });
    const result = await streamJob(port, "G28\nG1 X10\nG1 X20\n", { baudRate: 115200 });

    expect(result.ok).toBe(true);
    expect(received.map(commandOf)).toContain("G1 X20");
  });

  it("refuses a resend for a line that is not part of this job", async () => {
    // The defect this pins. Clamping only the bottom let a device-supplied
    // number past the end of the job end the loop with nothing recorded as
    // wrong — so the job stopped early, the cool-down never ran, and it
    // reported success over a part with a hot nozzle still parked above it.
    const { port, received } = fakePrinter((line) =>
      commandOf(line) === "G1 X10" ? ["Resend: 99", "ok"] : ["ok"],
    );
    const result = await streamJob(port, "G28\nG1 X10\n", { baudRate: 115200 });

    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/not part of this job/);
    for (const command of COOL_DOWN) expect(received).toContain(command);
  });

  it("refuses a resend for line zero", async () => {
    // The other end of the same range. Line numbering starts at 1; `N0` is the
    // reset this sends before the job.
    const { port } = fakePrinter((line) =>
      commandOf(line) === "G28" ? ["Resend: 0", "ok"] : ["ok"],
    );
    const result = await streamJob(port, "G28\n", { baudRate: 115200 });

    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/not part of this job/);
  });

  it("gives up when the printer keeps asking for the same line", async () => {
    // A printer stuck on one line answers every read and never advances, so no
    // timeout and no error ever ends the loop. Without a cap this runs for as
    // long as the tab is open.
    const { port, received } = fakePrinter((line) =>
      commandOf(line) === "G28" ? ["Resend: 1", "ok"] : ["ok"],
    );
    const result = await streamJob(port, "G28\n", { baudRate: 115200 });

    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/times running/);
    expect(received.map(commandOf).filter((c) => c === "G28")).toHaveLength(
      MAX_CONSECUTIVE_RESENDS + 1,
    );
    for (const command of COOL_DOWN) expect(received).toContain(command);
  });

  it("gives up when the printer reports an error", async () => {
    const { port } = fakePrinter((line) =>
      commandOf(line) === "G1 X10" ? ["Error:Printer halted"] : ["ok"],
    );
    const result = await streamJob(port, "G28\nG1 X10\n", { baudRate: 115200 });
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/refused the job/);
  });

  it("refuses to start a job the printer will not number", async () => {
    // `M110 N0` is the one exchange everything below it depends on. A printer
    // that will not take it disagrees about every line after.
    const { port } = fakePrinter((line) =>
      commandOf(line) === "M110 N0" ? ["Error:Unknown command"] : ["ok"],
    );
    const result = await streamJob(port, "G28\n", { baudRate: 115200 });
    expect(result.ok).toBe(false);
    expect(result.detail).toMatch(/would not start a numbered job/);
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

  it("does not hang for ever on a write that never drains", async () => {
    // A device powered off while still enumerated accepts no more bytes and
    // says nothing about it. The read side was made interruptible first; this
    // is the same failure one layer over, and before the write side took the
    // signal it was a tab the reader had to reload.
    const port: PrinterPort = {
      async open() {},
      async close() {},
      readable: new ReadableStream<Uint8Array>({ start() {} }),
      writable: new WritableStream<Uint8Array>({ write: () => new Promise<void>(() => {}) }),
    };
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 50);

    const result = await streamJob(port, "G28\n", {
      baudRate: 115200,
      signal: controller.signal,
    });

    expect(result.stopped).toBe(true);
  }, 20_000);

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

    it("says so plainly when the heaters could not be turned off", async () => {
      // A pulled cable is the likeliest reason a print stops, and it is exactly
      // the case where every cool-down write fails. A green "the heaters were
      // turned off" there asserts a safety action that did not happen, on the
      // one path where it matters most.
      const { port } = fakePrinter(
        (line) => (commandOf(line) === "G28" ? ["Error:Thermal runaway"] : ["ok"]),
        { refuseWrite: (line) => (COOL_DOWN as readonly string[]).includes(line) },
      );
      const result = await streamJob(port, "G28\n", { baudRate: 115200 });

      expect(result.ok).toBe(false);
      expect(result.detail).toMatch(/check its temperature/);
      expect(result.detail).not.toMatch(/heaters were turned off/);
    });

    it("leaves them alone when the job finished", async () => {
      // A finished job has already run whatever end G-code the slicer wrote.
      const { port, received } = fakePrinter(ackEverything);
      await streamJob(port, "G28\n", { baudRate: 115200 });
      expect(received).not.toContain("M104 S0");
    });
  });
});

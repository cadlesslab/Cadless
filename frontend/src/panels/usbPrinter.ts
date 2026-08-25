/** Talking to a 3D printer over USB, from the page.
 *
 * The server cannot reach a printer on somebody's own network. The browser can,
 * because it is already there — the Web Serial API opens a byte stream to a USB
 * device from an HTTPS page, given a user gesture and a per-device permission.
 * So the sliced job the server produced goes out through here instead of into a
 * downloads folder.
 *
 * **Port acquisition and the protocol are separate on purpose.** Only
 * `requestPrinterPort` touches the browser; everything below it works against
 * the `PrinterPort` shape, which a real `SerialPort` satisfies and a test can
 * supply with a pair of streams. The half worth testing is the conversation,
 * and it is the half that never needs a device.
 *
 * The conversation is Marlin's: numbered lines with a checksum, one `ok` back
 * per line, and a `Resend:` when the printer did not like what it heard. That
 * is more than a naive streamer does, and it is the difference between a
 * corrupted line being caught and being printed.
 */

/** The part of a serial port this needs.
 *
 * Declared structurally rather than imported: TypeScript ships no Web Serial
 * types, and a full transcription of the specification would be a copy to keep
 * in step for no gain. A real `SerialPort` satisfies this.
 */
export interface PrinterPort {
  open(options: { baudRate: number }): Promise<void>;
  close(): Promise<void>;
  readonly readable: ReadableStream<Uint8Array> | null;
  readonly writable: WritableStream<Uint8Array> | null;
}

declare global {
  interface Navigator {
    /** Present only where the browser implements Web Serial. */
    readonly serial?: { requestPort(): Promise<PrinterPort> };
  }
}

/** The rates a consumer printer is most likely to be listening at.
 *
 * Tried in turn rather than asked of the user: the answer is a property of
 * their firmware, they mostly do not know it, and finding out costs one
 * exchange per candidate.
 */
export const BAUD_CANDIDATES = [115200, 250000, 57600, 230400] as const;

/** How long to wait for a printer to say anything at all before giving up on a
 * baud rate. Short, because a wrong rate answers with silence or noise and
 * there are several to get through. */
export const HANDSHAKE_TIMEOUT_MS = 2500;

/** How long a single line may go unacknowledged before the stream is abandoned.
 * Generous: a printer that is heating, homing or clearing its buffer can stay
 * quiet for a while, and `busy:` is advisory rather than guaranteed. */
export const ACK_TIMEOUT_MS = 90_000;

/** Whether this browser can do it at all. Chrome, Edge and Opera can; Firefox
 * from 151; Safari does not. */
export function isUsbPrintingSupported(): boolean {
  return typeof navigator !== "undefined" && "serial" in navigator;
}

/** Ask the person to choose a device. Must be called from a user gesture. */
export async function requestPrinterPort(): Promise<PrinterPort> {
  if (!navigator.serial) throw new Error("This browser cannot talk to USB devices.");
  return navigator.serial.requestPort();
}

/** Marlin's line checksum: XOR of every byte before the `*`. */
export function checksum(line: string): number {
  let sum = 0;
  for (let i = 0; i < line.length; i += 1) sum ^= line.charCodeAt(i);
  return sum & 0xff;
}

/** One command, numbered and checksummed, the way a host is expected to send it.
 *
 * Numbering is what makes `Resend:` mean anything — without it a printer that
 * mis-heard a line has no way to ask for that line again, and the only options
 * are to ignore the problem or to stop.
 */
export function framed(lineNumber: number, command: string): string {
  const body = `N${lineNumber} ${command}`;
  return `${body}*${checksum(body)}`;
}

/** Strip what the printer does not need to hear.
 *
 * Comments and blank lines are most of a sliced file by count. A printer
 * ignores them, but every one still costs a round trip at 115200 baud, and the
 * job is long enough already.
 */
export function printableLines(gcode: string): string[] {
  const out: string[] = [];
  for (const raw of gcode.split(/\r?\n/)) {
    const line = raw.split(";")[0].trim();
    if (line) out.push(line);
  }
  return out;
}

/** Reads a port and hands back whole lines. */
class LineReader {
  private reader: ReadableStreamDefaultReader<Uint8Array>;
  private decoder = new TextDecoder();
  private buffer = "";
  private queued: string[] = [];

  constructor(readable: ReadableStream<Uint8Array>) {
    this.reader = readable.getReader();
  }

  /** The next line, or `null` if the port closed, the wait ran out, or the
   * caller asked to stop.
   *
   * The signal is not a convenience. Without it the only way out of this wait
   * is `timeoutMs`, which is `ACK_TIMEOUT_MS` on the hot path -- so a reader
   * who pressed Stop went on waiting up to a minute and a half for a printer
   * that was answering perfectly well. Checking `aborted` between lines cannot
   * fix that, because between lines is exactly where the code is not.
   */
  async next(timeoutMs: number, signal?: AbortSignal): Promise<string | null> {
    while (this.queued.length === 0) {
      if (signal?.aborted) return null;
      // Both losers of this race mean the same thing to the caller -- stop
      // waiting -- so they resolve to the same shape as a closed stream.
      let stopWaiting = () => {};
      const ended = new Promise<{ done: true; value: undefined }>((resolve) => {
        const finish = () => resolve({ done: true, value: undefined });
        const timer = setTimeout(finish, timeoutMs);
        const onAbort = () => {
          clearTimeout(timer);
          finish();
        };
        signal?.addEventListener("abort", onAbort, { once: true });
        // Taken down whichever way the race ends. A timer and a listener per
        // line, left behind, is tens of thousands of each over a real job.
        stopWaiting = () => {
          clearTimeout(timer);
          signal?.removeEventListener("abort", onAbort);
        };
      });
      const chunk = await Promise.race([this.reader.read(), ended]);
      stopWaiting();
      if (chunk.done || !chunk.value) return null;
      this.buffer += this.decoder.decode(chunk.value, { stream: true });
      const parts = this.buffer.split(/\r?\n/);
      this.buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (line) this.queued.push(line);
      }
    }
    return this.queued.shift() ?? null;
  }

  async release(): Promise<void> {
    try {
      await this.reader.cancel();
    } catch {
      // The port may already be gone; nothing here can act on that.
    }
    this.reader.releaseLock();
  }
}

function writerFor(port: PrinterPort) {
  if (!port.writable) throw new Error("The port cannot be written to.");
  const writer = port.writable.getWriter();
  const encoder = new TextEncoder();
  return {
    async send(line: string) {
      await writer.write(encoder.encode(`${line}\n`));
    },
    release() {
      writer.releaseLock();
    },
  };
}

/** What a printer said about itself, or why this is not one. */
export interface Handshake {
  ok: boolean;
  /** The rate it answered at, so the stream can reuse it. */
  baudRate?: number;
  /** Whatever it reported — firmware name and capabilities, usually. */
  firmware?: string;
  detail?: string;
}

/** Ask the device what it is, before sending it anything that moves.
 *
 * `M115` is the question every Marlin-class firmware answers, and the answer is
 * doing two jobs here: it settles the baud rate, and it settles that there is a
 * printer on the other end at all. A serial port is just a port — a debug
 * console, a modem, an Arduino running something else — and streaming G-code
 * into one of those is worth one exchange to avoid.
 */
export async function handshake(
  port: PrinterPort,
  bauds: readonly number[] = BAUD_CANDIDATES,
): Promise<Handshake> {
  for (const baudRate of bauds) {
    try {
      await port.open({ baudRate });
    } catch (err) {
      return { ok: false, detail: `The port would not open: ${(err as Error).message}` };
    }
    if (!port.readable || !port.writable) {
      await port.close();
      return { ok: false, detail: "The port opened but carries no stream." };
    }

    const reader = new LineReader(port.readable);
    const writer = writerFor(port);
    try {
      await writer.send("M115");
      const deadline = Date.now() + HANDSHAKE_TIMEOUT_MS;
      while (Date.now() < deadline) {
        const line = await reader.next(HANDSHAKE_TIMEOUT_MS);
        if (line === null) break;
        if (/FIRMWARE_NAME|Marlin|Klipper|RepRap/i.test(line)) {
          return { ok: true, baudRate, firmware: line };
        }
      }
    } finally {
      writer.release();
      await reader.release();
      await port.close();
    }
  }
  return {
    ok: false,
    detail:
      "Nothing on that port answered like a printer. Check it is switched on and connected " +
      "by USB, and that no other program — a slicer, a printer host — is holding the port.",
  };
}

export interface StreamProgress {
  sent: number;
  total: number;
}

export interface StreamResult {
  ok: boolean;
  detail: string;
  /** True when it stopped because the caller asked, rather than because it broke. */
  stopped?: boolean;
}

/** The commands that make a printer safe to walk away from.
 *
 * Sent when a stream stops early. Abandoning a print leaves the nozzle at
 * temperature over the part, which is the one outcome worth spending three
 * commands to avoid.
 */
export const COOL_DOWN = ["M104 S0", "M140 S0", "M107"] as const;

/** Stream a sliced job to an already-identified printer.
 *
 * One line at a time, each numbered and checksummed, each waiting for its `ok`.
 * That is slower than filling the printer's buffer, and it is what makes a
 * mis-heard line recoverable: the printer asks for a line number again and this
 * rewinds to it.
 */
export async function streamJob(
  port: PrinterPort,
  gcode: string,
  options: {
    baudRate: number;
    signal?: AbortSignal;
    onProgress?: (progress: StreamProgress) => void;
  },
): Promise<StreamResult> {
  const lines = printableLines(gcode);
  if (lines.length === 0) return { ok: false, detail: "That job has nothing to print." };

  try {
    await port.open({ baudRate: options.baudRate });
  } catch (err) {
    return { ok: false, detail: `The port would not open: ${(err as Error).message}` };
  }
  if (!port.readable || !port.writable) {
    await port.close();
    return { ok: false, detail: "The port opened but carries no stream." };
  }

  const reader = new LineReader(port.readable);
  const writer = writerFor(port);
  let index = 0;
  let stopped = false;
  let failure = "";

  try {
    // Reset the printer's idea of the line number so the numbering below starts
    // from a known place rather than from whatever the last host left behind.
    await writer.send(framed(0, "M110 N0"));
    await reader.next(ACK_TIMEOUT_MS, options.signal);

    while (index < lines.length) {
      if (options.signal?.aborted) {
        stopped = true;
        break;
      }
      const lineNumber = index + 1;
      await writer.send(framed(lineNumber, lines[index]));

      let acknowledged = false;
      while (!acknowledged) {
        const reply = await reader.next(ACK_TIMEOUT_MS, options.signal);
        if (options.signal?.aborted) {
          // Checked before the null: an aborted wait and a silent printer both
          // arrive as `null`, and telling the reader their printer died when
          // they were the one who stopped it would be a lie.
          stopped = true;
          break;
        }
        if (reply === null) {
          failure = "The printer stopped answering.";
          break;
        }
        const resend = /^(?:Resend|rs)[: ]\s*(\d+)/i.exec(reply);
        if (resend) {
          // It mis-heard. Rewind to the line it asked for; the checksum is what
          // let it notice, and the numbering is what lets this answer.
          index = Math.max(0, Number(resend[1]) - 1);
          acknowledged = true;
          break;
        }
        if (/^ok\b/i.test(reply)) {
          index += 1;
          options.onProgress?.({ sent: index, total: lines.length });
          acknowledged = true;
          break;
        }
        if (/^Error/i.test(reply)) {
          failure = `The printer refused the job: ${reply}`;
          break;
        }
        // Anything else — `busy: processing`, a temperature report, chatter —
        // is not an acknowledgement and not a failure. Keep waiting.
      }
      if (failure || stopped) break;
    }
  } catch (err) {
    failure = `The connection failed: ${(err as Error).message}`;
  } finally {
    if (stopped || failure) {
      for (const command of COOL_DOWN) {
        try {
          await writer.send(command);
        } catch {
          // The port is already gone, which is the case this cannot help.
        }
      }
    }
    writer.release();
    await reader.release();
    await port.close();
  }

  if (stopped) return { ok: false, stopped: true, detail: "Stopped. The heaters were turned off." };
  if (failure) return { ok: false, detail: failure };
  return { ok: true, detail: `Sent ${lines.length} lines.` };
}

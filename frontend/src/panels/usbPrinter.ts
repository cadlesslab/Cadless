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
 * recovery is the reason for the numbering, and getting it right means reading
 * the three lines Marlin actually sends rather than the one it is often
 * described as sending — see `RESEND_PRELUDE`.
 *
 * **Every wait in here is bounded and interruptible.** A print is hours long
 * and the only control the reader has is Stop, so a wait that Stop cannot reach
 * is a tab they have to reload. That applies to reads, to writes, and to
 * opening the port.
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

/** How long one write may sit undrained before the stream gives up on it.
 * A device powered off while still enumerated accepts no more bytes and reports
 * nothing, so an unbounded write is a hang with no way out of it. */
export const WRITE_TIMEOUT_MS = 30_000;

/** How long each cool-down write may take.
 *
 * Far shorter than an ordinary write, because these run *after* the job has
 * already stopped and the reader is watching a dialog that cannot close until
 * they finish. A device that has genuinely gone must not hold the tab for a
 * minute and a half proving it.
 */
export const COOL_DOWN_TIMEOUT_MS = 2000;

/** How long opening a port may take. Not a cancellation — nothing here can
 * withdraw an `open()` the browser has begun — but it stops *this* module
 * waiting, which is what somebody watching a dialog needs. */
export const OPEN_TIMEOUT_MS = 10_000;

/** How long to wait, after a `Resend:`, for the `ok` Marlin sends behind it.
 * Short and non-fatal: not every firmware sends one, and blocking on a line
 * that is never coming would cost `ACK_TIMEOUT_MS` for nothing. */
export const RESEND_SETTLE_MS = 2000;

/** How many times running one line may be asked for again before the job is
 * given up on. A printer stuck asking for the same line answers every read and
 * never advances, so nothing else in the loop ends it. */
export const MAX_CONSECUTIVE_RESENDS = 5;

/** The `Error:` lines that are the preamble to a `Resend:`, not a reason to stop.
 *
 * Marlin's `gcode_line_error` opens with `SERIAL_ERROR_START()`, so a mis-heard
 * line arrives as **three** lines — the error, the resend, then `ok` — and only
 * the middle one says what to do. Reading the first as fatal ends the print on
 * the first corrupted byte, which is precisely the failure the checksums exist
 * to survive: the recovery would never once have run against real firmware.
 */
const RESEND_PRELUDE =
  /checksum mismatch|Line Number is not Last Line Number|No Checksum with line number|expected line/i;

/** A printer asking for a line again. `rs` is the older spelling. */
const RESEND_REQUEST = /^(?:Resend|rs)[: ]\s*(\d+)/i;

/** Whether this browser can do it at all. Not every browser implements Web
 * Serial, and the ones that do not include a major desktop browser — so this is
 * a real branch rather than a formality. */
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

/** Awaiting something that may never finish, without waiting for ever.
 *
 * Returns `work`'s value, or `fallback` if the deadline or the stop signal got
 * there first. **Losing the race does not cancel `work`** — nothing in Web
 * Serial can be withdrawn once begun — so a caller that must not lose what
 * `work` eventually produces has to keep hold of the promise itself, which is
 * what `LineReader.pending` is for.
 */
async function raceDeadline<T, F>(
  work: Promise<T>,
  ms: number,
  fallback: F,
  signal?: AbortSignal,
): Promise<T | F> {
  let stopWaiting = () => {};
  const gaveUp = new Promise<F>((resolve) => {
    const finish = () => resolve(fallback);
    const timer = setTimeout(finish, ms);
    const onAbort = () => {
      clearTimeout(timer);
      finish();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    stopWaiting = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    };
  });
  try {
    return await Promise.race([work, gaveUp]);
  } finally {
    // In a `finally` rather than after the race, so a rejected `work` takes the
    // timer and the listener down with it too. One of each per line, left
    // behind, is tens of thousands of both over a real job.
    stopWaiting();
  }
}

/** The value `raceDeadline` yields when a read did not arrive in time. */
const NO_CHUNK = Symbol("no chunk");

/** Reads a port and hands back whole lines. Never throws: a port that has gone
 * away is reported as "no next line", which every caller already handles. */
class LineReader {
  private reader: ReadableStreamDefaultReader<Uint8Array>;
  private decoder = new TextDecoder();
  private buffer = "";
  private queued: string[] = [];
  /** The read that is already in flight.
   *
   * Held here rather than started fresh each time, because a stream with a
   * pending read request hands the next chunk **straight to it**: a read
   * abandoned when the deadline won the race would swallow bytes that then
   * never reach `buffer`, and the caller would have no way to know. Keeping it
   * means a wait that times out costs nothing but the wait.
   */
  private pending: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;

  constructor(readable: ReadableStream<Uint8Array>) {
    this.reader = readable.getReader();
  }

  /** A line already received and not yet handed out, without waiting for one. */
  peek(): string | null {
    return this.queued[0] ?? null;
  }

  /** Discard the line `peek` would have returned. */
  drop(): void {
    this.queued.shift();
  }

  /** The next line, or `null` if the port closed, the wait ran out, or the
   * caller asked to stop.
   *
   * The signal is not a convenience. Without it the only way out of this wait
   * is `timeoutMs`, which is `ACK_TIMEOUT_MS` on the hot path — so a reader who
   * pressed Stop went on waiting up to a minute and a half for a printer that
   * was answering perfectly well. Checking `aborted` between lines cannot fix
   * that, because between lines is exactly where the code is not.
   */
  async next(timeoutMs: number, signal?: AbortSignal): Promise<string | null> {
    while (this.queued.length === 0) {
      if (signal?.aborted) return null;
      this.pending ??= this.reader.read().finally(() => {
        this.pending = null;
      });
      let chunk: ReadableStreamReadResult<Uint8Array> | typeof NO_CHUNK;
      try {
        chunk = await raceDeadline(this.pending, timeoutMs, NO_CHUNK, signal);
      } catch {
        // The port went away mid-read. The same answer as a closed stream:
        // there is no next line.
        return null;
      }
      if (chunk === NO_CHUNK || chunk.done || !chunk.value) return null;
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
    try {
      this.reader.releaseLock();
    } catch {
      // Same.
    }
  }
}

function writerFor(port: PrinterPort) {
  if (!port.writable) throw new Error("The port cannot be written to.");
  const writer = port.writable.getWriter();
  const encoder = new TextEncoder();
  return {
    /** Send one line. `false` means it did not go out.
     *
     * Bounded for the same reason reads are: a device that stops draining —
     * powered off while still enumerated, stuck flow control — leaves `write`
     * pending for ever, and an unbounded write is a hang that Stop cannot
     * reach and the progress dialog cannot be dismissed out of.
     */
    async send(
      line: string,
      signal?: AbortSignal,
      timeoutMs: number = WRITE_TIMEOUT_MS,
    ): Promise<boolean> {
      try {
        return await raceDeadline(
          writer.write(encoder.encode(`${line}\n`)).then(() => true),
          timeoutMs,
          false,
          signal,
        );
      } catch {
        return false;
      }
    },
    release() {
      try {
        writer.releaseLock();
      } catch {
        // The stream may already be gone.
      }
    },
  };
}

/** Take a reader and a writer, or leave the port exactly as it was found.
 *
 * Acquiring these outside the block that closes the port was a leak with no way
 * back: `getReader()` throws on an already-locked stream, and a port whose
 * readable is locked cannot be closed either — so a half-acquired port stayed
 * open, locked, and unusable until the tab was reloaded.
 */
async function acquire(
  port: PrinterPort,
): Promise<{ reader: LineReader; writer: ReturnType<typeof writerFor> } | string> {
  let reader: LineReader | undefined;
  let writer: ReturnType<typeof writerFor> | undefined;
  try {
    if (!port.readable || !port.writable) throw new Error("the port carries no stream");
    reader = new LineReader(port.readable);
    writer = writerFor(port);
    return { reader, writer };
  } catch (err) {
    writer?.release();
    if (reader) await reader.release();
    try {
      await port.close();
    } catch {
      // Nothing further to try.
    }
    return `The port could not be used: ${(err as Error).message}`;
  }
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
  signal?: AbortSignal,
): Promise<Handshake> {
  for (const baudRate of bauds) {
    if (signal?.aborted) return { ok: false, detail: "Stopped before a printer answered." };

    let opened: boolean;
    try {
      opened = await raceDeadline(
        port.open({ baudRate }).then(() => true),
        OPEN_TIMEOUT_MS,
        false,
        signal,
      );
    } catch (err) {
      return { ok: false, detail: `The port would not open: ${(err as Error).message}` };
    }
    if (!opened) {
      return { ok: false, detail: "The port did not finish opening." };
    }

    const held = await acquire(port);
    if (typeof held === "string") return { ok: false, detail: held };
    const { reader, writer } = held;

    let answer: Handshake | null = null;
    try {
      await writer.send("M115", signal);
      const deadline = Date.now() + HANDSHAKE_TIMEOUT_MS;
      for (;;) {
        // The time left, not the whole budget again: passing the full window on
        // every iteration let a chatty printer stretch one candidate to several
        // times its share.
        const remaining = deadline - Date.now();
        if (remaining <= 0) break;
        const line = await reader.next(remaining, signal);
        if (line === null) break;
        if (/FIRMWARE_NAME|Marlin|Klipper|RepRap/i.test(line)) {
          answer = { ok: true, baudRate, firmware: line };
          break;
        }
      }
    } finally {
      writer.release();
      await reader.release();
      try {
        await port.close();
      } catch {
        // A close that fails must not turn a printer that answered into a
        // failure: this ran inside `finally`, so a throw here replaced the
        // answer above with an exception.
      }
    }
    if (answer) return answer;
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

  let opened: boolean;
  try {
    opened = await raceDeadline(
      port.open({ baudRate: options.baudRate }).then(() => true),
      OPEN_TIMEOUT_MS,
      false,
      options.signal,
    );
  } catch (err) {
    return { ok: false, detail: `The port would not open: ${(err as Error).message}` };
  }
  if (!opened) return { ok: false, detail: "The port did not finish opening." };

  const held = await acquire(port);
  if (typeof held === "string") return { ok: false, detail: held };
  const { reader, writer } = held;

  let index = 0;
  let stopped = false;
  let failure = "";
  let cooledDown = false;
  let repeatedTarget: number | null = null;
  let repeats = 0;

  try {
    // Reset the printer's idea of the line number so the numbering below starts
    // from a known place rather than from whatever the last host left behind.
    // This is the one exchange the rest of the conversation is built on, so its
    // answer is read rather than discarded.
    await writer.send(framed(0, "M110 N0"), options.signal);
    const reset = await reader.next(ACK_TIMEOUT_MS, options.signal);
    if (reset !== null && /^Error/i.test(reset) && !RESEND_PRELUDE.test(reset)) {
      failure = `The printer would not start a numbered job: ${reset}`;
    }

    while (!failure && index < lines.length) {
      if (options.signal?.aborted) {
        stopped = true;
        break;
      }
      const lineNumber = index + 1;
      if (!(await writer.send(framed(lineNumber, lines[index]), options.signal))) {
        if (options.signal?.aborted) stopped = true;
        else failure = "The printer stopped accepting data.";
        break;
      }

      // What the printer asked for, if it asked. Collected during the wait
      // rather than acted on inside it, because Marlin's `Resend:` is followed
      // by an `ok` and that `ok` has to be consumed here — read as the
      // acknowledgement of the *re-sent* line, it would put every ack after it
      // one line out for the rest of the job.
      let resendTo: number | null = null;
      for (;;) {
        const reply = await reader.next(
          resendTo === null ? ACK_TIMEOUT_MS : RESEND_SETTLE_MS,
          options.signal,
        );
        if (options.signal?.aborted) {
          // Before the null: an aborted wait and a silent printer both arrive
          // as `null`, and telling the reader their printer died when they were
          // the one who stopped it would be a lie.
          stopped = true;
          break;
        }
        if (reply === null) {
          // Waiting on the `ok` behind a resend is best-effort: not every
          // firmware sends one, and carrying on is better than stalling.
          if (resendTo !== null) break;
          failure = "The printer stopped answering.";
          break;
        }

        const asked = RESEND_REQUEST.exec(reply);
        if (asked) {
          resendTo = Number(asked[1]);
          continue;
        }
        if (/^ok\b/i.test(reply)) break;
        if (/^Error/i.test(reply)) {
          // Not fatal when it is the sentence Marlin puts in front of a
          // `Resend:` — see RESEND_PRELUDE. Keep listening for the line that
          // says what to do.
          if (RESEND_PRELUDE.test(reply)) continue;
          failure = `The printer refused the job: ${reply}`;
          break;
        }
        // Anything else — `busy: processing`, a temperature report, chatter —
        // is not an acknowledgement and not a failure. Keep waiting.
      }
      if (stopped || failure) break;

      if (resendTo === null) {
        repeatedTarget = null;
        repeats = 0;
        index += 1;
        options.onProgress?.({ sent: index, total: lines.length });
        continue;
      }

      // A resend target outside what has actually been sent is not an
      // instruction to obey. Clamping only the bottom let a number past the end
      // of the job end the loop with nothing recorded as wrong — so the job
      // stopped early, the cool-down never ran, and it reported success.
      if (resendTo < 1 || resendTo > index + 1) {
        failure = `The printer asked for line ${resendTo}, which is not part of this job.`;
        break;
      }
      repeats = resendTo === repeatedTarget ? repeats + 1 : 1;
      repeatedTarget = resendTo;
      if (repeats > MAX_CONSECUTIVE_RESENDS) {
        // A printer stuck asking for one line answers every read and never
        // advances, so no timeout and no error ever ends the loop.
        failure = `The printer asked for line ${resendTo} ${repeats} times running.`;
        break;
      }
      index = resendTo - 1;
    }
  } catch (err) {
    failure = `The connection failed: ${(err as Error).message}`;
  } finally {
    if (stopped || failure || index < lines.length) {
      let delivered = 0;
      for (const command of COOL_DOWN) {
        // Deliberately without the signal: on the Stop path it is already
        // aborted, and passing it would make every one of these fail on the one
        // path they exist for. And stop at the first refusal -- if one cannot go
        // out the port is gone, and the other two are only a wait the reader
        // spends in front of a dialog that cannot be closed.
        if (!(await writer.send(command, undefined, COOL_DOWN_TIMEOUT_MS))) break;
        delivered += 1;
      }
      cooledDown = delivered === COOL_DOWN.length;
    }
    writer.release();
    await reader.release();
    try {
      await port.close();
    } catch {
      // Already gone; there is nothing this can do about it.
    }
  }

  // Said from what actually went out, not from having reached this line. Every
  // cool-down write is allowed to fail — the cable is the likeliest reason a
  // print stops — and a green "the heaters were turned off" on the one path
  // where they were not is worse than saying nothing.
  const heaters = cooledDown
    ? "The heaters were turned off."
    : "The printer stopped answering, so check its temperature at the machine.";

  if (stopped) return { ok: false, stopped: true, detail: `Stopped. ${heaters}` };
  if (failure) return { ok: false, detail: `${failure} ${heaters}` };
  if (index < lines.length) {
    return { ok: false, detail: `The job ended after ${index} of ${lines.length} lines. ${heaters}` };
  }
  return { ok: true, detail: `Sent ${lines.length} lines.` };
}

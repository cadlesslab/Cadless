import { describe, expect, it } from "vitest";

import type { ProgressEvent } from "../api";
import { summarizeProgress } from "./progress";

describe("summarizeProgress", () => {
  it("is idle with no events", () => {
    const s = summarizeProgress([]);
    expect(s.state).toBe("idle");
    expect(s.steps.every((x) => x.status === "pending")).toBe(true);
  });

  it("marks earlier steps done and the current step active while running", () => {
    const events: ProgressEvent[] = [
      { event: "start", intent: "x", max_tries: 3, mode: "generate" },
      { event: "stage", phase: "interpret", status: "ok", attempt: 1 },
      { event: "stage", phase: "generate", status: "ok", attempt: 1 },
      { event: "stage", phase: "build", status: "begin", attempt: 1 },
    ];
    const s = summarizeProgress(events);
    expect(s.state).toBe("running");
    const byKey = Object.fromEntries(s.steps.map((x) => [x.key, x.status]));
    expect(byKey.interpret).toBe("done");
    expect(byKey.generate).toBe("done");
    expect(byKey.validate).toBe("done"); // implicitly done (before build)
    expect(byKey.build).toBe("active");
    expect(byKey.mesh).toBe("pending");
  });

  it("reports success on done.ok and marks all steps done", () => {
    const events: ProgressEvent[] = [
      { event: "stage", phase: "interpret", status: "ok", attempt: 1 },
      { event: "stage", phase: "mesh", status: "ok", attempt: 1 },
      { event: "done", version_id: 9, ok: true, attempt_count: 1 },
    ];
    const s = summarizeProgress(events);
    expect(s.state).toBe("success");
    expect(s.steps.every((x) => x.status === "done")).toBe(true);
  });

  it("surfaces the failing stage on error and tracks attempt count", () => {
    const events: ProgressEvent[] = [
      { event: "stage", phase: "generate", status: "ok", attempt: 1 },
      { event: "stage", phase: "build", status: "error", attempt: 2, error: "OCCT boom" },
      { event: "done", version_id: 5, ok: false, attempt_count: 2 },
    ];
    const s = summarizeProgress(events);
    expect(s.state).toBe("error");
    expect(s.errorText).toBe("OCCT boom");
    expect(s.attempt).toBe(2);
    expect(s.steps.find((x) => x.key === "build")?.status).toBe("error");
  });

  it("handles a transport error event", () => {
    const s = summarizeProgress([{ event: "error", detail: "connection lost" }]);
    expect(s.state).toBe("error");
    expect(s.errorText).toBe("connection lost");
  });
});

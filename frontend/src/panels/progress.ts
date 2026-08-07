/** Derive a staged-progress view-model from the SSE event stream.
 * Pure — unit-tested. Consumes the structured `stage` events from while
 * staying tolerant of the legacy start/attempt/done/error events. */
import type { ProgressEvent } from "../api";

export type StepStatus = "pending" | "active" | "done" | "error";

export interface Step {
  key: string;
  label: string;
  status: StepStatus;
}

export interface ProgressSummary {
  state: "idle" | "running" | "success" | "error";
  steps: Step[];
  attempt: number;
  errorText?: string;
}

const STEP_DEFS: { key: string; label: string; phases: string[] }[] = [
  { key: "interpret", label: "Interpret", phases: ["interpret"] },
  { key: "generate", label: "Generate", phases: ["generate", "refine"] },
  { key: "validate", label: "Validate", phases: ["validate"] },
  { key: "build", label: "Build", phases: ["build"] },
  { key: "mesh", label: "Mesh", phases: ["mesh"] },
];

function statusFromStage(status: string): StepStatus {
  if (status === "ok") return "done";
  if (status === "error") return "error";
  return "active"; // "begin"
}

export function summarizeProgress(events: ProgressEvent[]): ProgressSummary {
  const doneEvent = events.find((e) => e.event === "done");
  const errorEvent = events.find((e) => e.event === "error");

  // Per-step status from the latest matching stage event.
  const ownStatus: Record<string, StepStatus> = {};
  let attempt = 1;
  for (const e of events) {
    if (e.event === "stage") {
      attempt = Math.max(attempt, e.attempt || 1);
      const step = STEP_DEFS.find((s) => s.phases.includes(e.phase));
      if (step) ownStatus[step.key] = statusFromStage(e.status);
    } else if (e.event === "attempt") {
      attempt = Math.max(attempt, e.n || 1);
    } else if (e.event === "done") {
      attempt = Math.max(attempt, e.attempt_count || 1);
    }
  }

  const lastTouched = STEP_DEFS.reduce(
    (acc, s, i) => (ownStatus[s.key] ? i : acc),
    -1,
  );

  const success = !!doneEvent && doneEvent.ok && !errorEvent;

  const steps: Step[] = STEP_DEFS.map((s, i) => {
    let status: StepStatus = ownStatus[s.key] ?? "pending";
    // steps before the furthest-touched one are implicitly done
    if (status === "pending" && i < lastTouched) status = "done";
    // on overall success, everything reads done
    if (success) status = "done";
    return { key: s.key, label: s.label, status };
  });

  let state: ProgressSummary["state"] = "idle";
  let errorText: string | undefined;
  if (errorEvent) {
    state = "error";
    errorText = errorEvent.detail;
  } else if (doneEvent && !doneEvent.ok) {
    state = "error";
    errorText = lastErrorText(events);
  } else if (success) {
    state = "success";
  } else if (events.length > 0) {
    state = "running";
  }

  return { state, steps, attempt, errorText };
}

function lastErrorText(events: ProgressEvent[]): string | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event === "stage" && e.status === "error" && e.error) return e.error;
    if (e.event === "attempt" && e.error) return e.error;
  }
  return "Generation failed.";
}

/** Staged generation progress + error recovery. */
import type { ProgressEvent } from "../api";
import { Button } from "../components";
import { summarizeProgress } from "./progress";

const STATUS_ICON: Record<string, string> = {
  done: "✓",
  active: "•",
  error: "✕",
  pending: "·",
};

export function StagedProgress({
  events,
  onRetry,
  onEdit,
}: {
  events: ProgressEvent[];
  onRetry: () => void;
  onEdit: () => void;
}) {
  const { state, steps, attempt, errorText } = summarizeProgress(events);
  if (state === "idle") return null;

  return (
    <div className={`staged staged-${state}`} aria-live="polite">
      <ol className="staged-steps">
        {steps.map((s) => (
          <li key={s.key} className={`staged-step step-${s.status}`}>
            <span className="staged-dot" aria-hidden>
              {STATUS_ICON[s.status]}
            </span>
            <span className="staged-label">{s.label}</span>
          </li>
        ))}
      </ol>

      <div className="staged-meta">
        {state === "running" && <span className="staged-note">Generating…</span>}
        {state === "success" && <span className="staged-note ok">Done</span>}
        {attempt > 1 && <span className="staged-attempt">attempt {attempt}</span>}
      </div>

      {state === "error" && (
        <div className="staged-error" role="alert">
          <p className="staged-error-text">{errorText}</p>
          <div className="staged-actions">
            <Button size="sm" variant="primary" onClick={onRetry}>
              Retry
            </Button>
            <Button size="sm" variant="ghost" onClick={onEdit}>
              Edit prompt
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

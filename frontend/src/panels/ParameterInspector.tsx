/** Editable parameter sliders. Reads version.parameters and re-runs
 * deterministically via reparametrize (no LLM) on commit. */
import { useEffect, useState } from "react";

import { Slider } from "../components";
import { useActiveVersion } from "../state";
import { useApp } from "../useApp";

/** A sensible slider range for a parameter given its current value. */
export function sliderRange(value: number): { min: number; max: number; step: number } {
  const max = Math.max(Math.ceil(Math.abs(value) * 2), 1);
  return { min: 0, max, step: max >= 20 ? 0.5 : 0.1 };
}

export function ParameterInspector({ readOnly = false }: { readOnly?: boolean } = {}) {
  const app = useApp();
  const version = useActiveVersion();
  const numeric = Object.entries(version?.parameters ?? {}).filter(
    ([, v]) => typeof v === "number",
  ) as [string, number][];

  const [draft, setDraft] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);

  // re-sync the editable copy whenever the active version changes
  useEffect(() => {
    setDraft(Object.fromEntries(numeric));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version?.id]);

  if (!version?.ok || numeric.length === 0) return null;

  // Catalog items are read-only: show each dimension as a static value, no sliders.
  if (readOnly) {
    return (
      <div className="params params-readonly">
        {numeric.map(([key, value]) => (
          <div className="param-row" key={key}>
            <div className="param-head">
              <span className="param-name">{key}</span>
              <span className="param-val">{value}</span>
            </div>
          </div>
        ))}
      </div>
    );
  }

  async function commit(key: string, value: number) {
    if (!version) return;
    setBusy(true);
    try {
      await app.reparametrize(version.id, { [key]: value });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="params">
      {busy && <p className="params-busy">rebuilding…</p>}
      {numeric.map(([key, orig]) => {
        const value = draft[key] ?? orig;
        const { min, max, step } = sliderRange(orig);
        return (
          <div className="param-row" key={key}>
            <div className="param-head">
              <span className="param-name">{key}</span>
              <span className="param-val">{value}</span>
            </div>
            <Slider
              label={key}
              min={min}
              max={Math.max(max, value)}
              step={step}
              value={value}
              onValueChange={(v) => setDraft((d) => ({ ...d, [key]: v }))}
              onValueCommit={(v) => commit(key, v)}
            />
          </div>
        );
      })}
    </div>
  );
}

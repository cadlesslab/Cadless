/** Pure version helpers: metrics comparison + lineage ordering. */
import type { Version } from "../api";

export interface MetricRow {
  label: string;
  a: number | null;
  b: number | null;
  delta: number | null;
}

function delta(a: number | null, b: number | null): number | null {
  return a != null && b != null ? b - a : null;
}

/** Volume + per-axis bbox comparison rows for two versions (b relative to a). */
export function compareMetrics(a: Version, b: Version): MetricRow[] {
  const rows: MetricRow[] = [
    { label: "Volume (mm³)", a: a.volume, b: b.volume, delta: delta(a.volume, b.volume) },
  ];
  (["X", "Y", "Z"] as const).forEach((axis, i) => {
    const av = a.bbox?.[i] ?? null;
    const bv = b.bbox?.[i] ?? null;
    rows.push({ label: `${axis} (mm)`, a: av, b: bv, delta: delta(av, bv) });
  });
  return rows;
}

/** Display label for a version: the user's annotation if set, else its prompt. */
export function versionLabel(v: Version, note?: string): string {
  return note?.trim() || v.prompt;
}
